// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
package main

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
	"unicode"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/log/global"
	semconv "go.opentelemetry.io/otel/semconv/v1.24.0"
	"go.opentelemetry.io/otel/trace"

	"github.com/IBM/sarama"
	"github.com/google/uuid"
	otelhooks "github.com/open-feature/go-sdk-contrib/hooks/open-telemetry/pkg"
	flagd "github.com/open-feature/go-sdk-contrib/providers/flagd/pkg"
	"github.com/open-feature/go-sdk/openfeature"
	"github.com/sony/gobreaker"

	"go.opentelemetry.io/contrib/bridges/otelslog"
	"go.opentelemetry.io/contrib/instrumentation/google.golang.org/grpc/otelgrpc"
	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
	"go.opentelemetry.io/contrib/instrumentation/runtime"
	"go.opentelemetry.io/otel"
	otelcodes "go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/exporters/otlp/otlplog/otlploggrpc"
	"go.opentelemetry.io/otel/exporters/otlp/otlpmetric/otlpmetricgrpc"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc"
	"go.opentelemetry.io/otel/propagation"

	sdklog "go.opentelemetry.io/otel/sdk/log"
	sdkmetric "go.opentelemetry.io/otel/sdk/metric"
	sdkresource "go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/health"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"

	flags "github.com/open-telemetry/opentelemetry-demo/src/checkout/flags"
	pb "github.com/open-telemetry/opentelemetry-demo/src/checkout/genproto/oteldemo"
	"github.com/open-telemetry/opentelemetry-demo/src/checkout/kafka"
	"github.com/open-telemetry/opentelemetry-demo/src/checkout/money"
)

// Validation helper functions

var (
	emailRegex   = regexp.MustCompile(`^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$`)
)

// ValidatePlaceOrderRequest validates PlaceOrderRequest against all AC requirements
func ValidatePlaceOrderRequest(req *pb.PlaceOrderRequest) error {
	// AC-1: Validate user_id is present
	if req.UserId == "" {
		return status.Errorf(codes.InvalidArgument, "user_id is a required field")
	}

	// AC-2: Validate email is present and format is correct
	if req.Email == "" || !emailRegex.MatchString(req.Email) {
		return status.Errorf(codes.InvalidArgument, "email is invalid or missing")
	}

	// AC-3: Validate order_items is not empty
	if len(req.Items) == 0 {
		return status.Errorf(codes.InvalidArgument, "order must contain at least one item")
	}

	// AC-4 & AC-5: Validate each order item has positive quantity and unit price
	for _, item := range req.Items {
		if item.Quantity <= 0 {
			return status.Errorf(codes.InvalidArgument, "item %s has invalid quantity: must be positive integer", item.ProductId)
		}
		if item.UnitPrice <= 0 {
			return status.Errorf(codes.InvalidArgument, "item %s has invalid price: must be positive value", item.ProductId)
		}
	}

	// AC-6: Validate shipping address is present
	if req.Address == nil {
		return status.Errorf(codes.InvalidArgument, "shipping address is required")
	}

	// AC-7: Validate all shipping address components are present
	addr := req.Address
	if addr.StreetAddress == "" {
		return status.Errorf(codes.InvalidArgument, "shipping address: street_address is required")
	}
	if addr.City == "" {
		return status.Errorf(codes.InvalidArgument, "shipping address: city is required")
	}
	if addr.ZipCode == "" {
		return status.Errorf(codes.InvalidArgument, "shipping address: zip_code is required")
	}
	if addr.Country == "" {
		return status.Errorf(codes.InvalidArgument, "shipping address: country is required")
	}

	// AC-8: All validations passed
	return nil
}

//go:generate go install google.golang.org/protobuf/cmd/protoc-gen-go
//go:generate go install google.golang.org/grpc/cmd/protoc-gen-go-grpc
//go:generate protoc --go_out=./ --go-grpc_out=./ --proto_path=../../pb ../../pb/demo.proto
//go:generate go install github.com/open-feature/cli/cmd/openfeature@v0.4.0
//go:generate openfeature generate -o flags --package-name flags go

var (
	logger            *slog.Logger
	tracer            trace.Tracer
	resource          *sdkresource.Resource
	initResourcesOnce sync.Once
)

func initResource() *sdkresource.Resource {
	initResourcesOnce.Do(func() {
		extraResources, _ := sdkresource.New(
			context.Background(),
			sdkresource.WithOS(),
			sdkresource.WithProcess(),
			sdkresource.WithContainer(),
			sdkresource.WithHost(),
		)
		resource, _ = sdkresource.Merge(
			sdkresource.Default(),
			extraResources,
		)
	})
	return resource
}

func initTracerProvider() *sdktrace.TracerProvider {
	ctx := context.Background()

	exporter, err := otlptracegrpc.New(ctx)
	if err != nil {
		logger.Error(fmt.Sprintf("new otlp trace grpc exporter failed: %v", err))
	}
	tp := sdktrace.NewTracerProvider(
		sdktrace.WithBatcher(exporter),
		sdktrace.WithResource(initResource()),
	)
	otel.SetTracerProvider(tp)
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(propagation.TraceContext{}, propagation.Baggage{}))
	return tp
}

func initMeterProvider() *sdkmetric.MeterProvider {
	ctx := context.Background()

	exporter, err := otlpmetricgrpc.New(ctx)
	if err != nil {
		logger.Error(fmt.Sprintf("new otlp metric grpc exporter failed: %v", err))
	}

	mp := sdkmetric.NewMeterProvider(
		sdkmetric.WithReader(sdkmetric.NewPeriodicReader(exporter)),
		sdkmetric.WithResource(initResource()),
	)
	otel.SetMeterProvider(mp)
	return mp
}

func initLoggerProvider() *sdklog.LoggerProvider {
	ctx := context.Background()

	logExporter, err := otlploggrpc.New(ctx)
	if err != nil {
		return nil
	}

	loggerProvider := sdklog.NewLoggerProvider(
		sdklog.WithProcessor(sdklog.NewBatchProcessor(logExporter)),
	)
	global.SetLoggerProvider(loggerProvider)

	return loggerProvider
}

type checkout struct {
	productCatalogSvcAddr string
	cartSvcAddr           string
	currencySvcAddr       string
	shippingSvcAddr       string
	emailSvcAddr          string
	paymentSvcAddr        string
	kafkaBrokerSvcAddr    string
	pb.UnimplementedCheckoutServiceServer
	KafkaProducerClient     sarama.AsyncProducer
	shippingSvcClient       pb.ShippingServiceClient
	productCatalogSvcClient pb.ProductCatalogServiceClient
	cartSvcClient           pb.CartServiceClient
	currencySvcClient       pb.CurrencyServiceClient
	emailSvcClient          pb.EmailServiceClient
	paymentSvcClient        pb.PaymentServiceClient
	httpClient              *http.Client
}

func main() {
	var port string
	mustMapEnv(&port, "CHECKOUT_PORT")
	// Get HTTP port for health endpoints, default to 8080 if not set
	httpPort := os.Getenv("CHECKOUT_HTTP_PORT")
	if httpPort == "" {
		httpPort = "8080"
	}

	// Parse rate limit configuration from environment variables
	rateLimitRPS := 10.0
	if rpsStr := os.Getenv("CHECKOUT_RATE_LIMIT_RPS"); rpsStr != "" {
		if parsedRPS, err := strconv.ParseFloat(rpsStr, 64); err == nil && parsedRPS > 0 {
			rateLimitRPS = parsedRPS
		}
	}

	rateLimitBurst := 20
	if burstStr := os.Getenv("CHECKOUT_RATE_LIMIT_BURST"); burstStr != "" {
		if parsedBurst, err := strconv.Atoi(burstStr); err == nil && parsedBurst > 0 {
			rateLimitBurst = parsedBurst
		}
	}

	// Initialize rate limiter middleware
	rateLimiterMw := NewRateLimiterMiddleware(rateLimitRPS, rateLimitBurst)

	// Initialize health check server first, set to NOT_SERVING during initialization
	healthcheck := health.NewServer()
	healthcheck.SetServingStatus("", healthpb.HealthCheckResponse_NOT_SERVING)

	// Initialize prometheus metrics
	registry := prometheus.NewRegistry()
	retryMetrics = NewRetryMetrics(registry)

	// Set up HTTP health endpoints first so they are available during initialization
	mux := http.NewServeMux()

	// Metrics endpoint
	mux.Handle("/metrics", promhttp.HandlerFor(registry, promhttp.HandlerOpts{}))

	// Health endpoint - always returns 200 when service is running
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("OK"))
	})

	// Ready endpoint - uses gRPC health check status
	mux.HandleFunc("/ready", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()

		resp, err := healthcheck.Check(ctx, &healthpb.HealthCheckRequest{})
		if err != nil || resp.Status != healthpb.HealthCheckResponse_SERVING {
			w.WriteHeader(http.StatusServiceUnavailable)
			w.Write([]byte("NOT_READY"))
			return
		}
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("READY"))
	})

	httpServer := &http.Server{
		Addr:    fmt.Sprintf(":%s", httpPort),
		Handler: rateLimiterMw(mux),
	}

	// Start HTTP server immediately so health endpoints are available during initialization
	go func() {
		logger.Info(fmt.Sprintf("HTTP server starting on port %s", httpPort))
		if err := httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			logger.Error(fmt.Sprintf("failed to start HTTP server: %v", err))
			os.Exit(1)
		}
	}()

	tp := initTracerProvider()
	defer func() {
		if err := tp.Shutdown(context.Background()); err != nil {
			logger.Error(fmt.Sprintf("Error shutting down tracer provider: %v", err))
		}
	}()

	mp := initMeterProvider()
	defer func() {
		if err := mp.Shutdown(context.Background()); err != nil {
			logger.Error(fmt.Sprintf("Error shutting down meter provider: %v", err))
		}
	}()

	lp := initLoggerProvider()
	defer func() {
		if err := lp.Shutdown(context.Background()); err != nil {
			logger.Error(fmt.Sprintf("Error shutting down logger provider: %v", err))
		}
	}()

	// this *must* be called after the logger provider is initialized
	// otherwise the Sarama producer in kafka/producer.go will not be
	// able to log properly
	logger = otelslog.NewLogger("checkout")
	slog.SetDefault(logger)

	err := runtime.Start(runtime.WithMinimumReadMemStatsInterval(time.Second))
	if err != nil {
		logger.Error((err.Error()))
	}

	provider, err := flagd.NewProvider()
	if err != nil {
		logger.Error("Error creating flagd provider", slog.Any("error", err))
	}

	err = openfeature.SetProvider(provider)
	if err != nil {
		logger.Error("Failed to set flagd as the provider", slog.Any("error", err))
	}
	defer openfeature.Shutdown()
	openfeature.AddHooks(otelhooks.NewTracesHook())

	tracer = tp.Tracer("checkout")

	svc := new(checkout)
	svc.httpClient = &http.Client{
		Transport: otelhttp.NewTransport(http.DefaultTransport),
	}

	mustMapEnv(&svc.shippingSvcAddr, "SHIPPING_ADDR")
	c := mustCreateClient(svc.shippingSvcAddr, "shipping")
	svc.shippingSvcClient = pb.NewShippingServiceClient(c)
	defer c.Close()

	mustMapEnv(&svc.productCatalogSvcAddr, "PRODUCT_CATALOG_ADDR")
	c = mustCreateClient(svc.productCatalogSvcAddr, "product-catalog")
	svc.productCatalogSvcClient = pb.NewProductCatalogServiceClient(c)
	defer c.Close()

	mustMapEnv(&svc.cartSvcAddr, "CART_ADDR")
	c = mustCreateClient(svc.cartSvcAddr, "cart")
	svc.cartSvcClient = pb.NewCartServiceClient(c)
	defer c.Close()

	mustMapEnv(&svc.currencySvcAddr, "CURRENCY_ADDR")
	c = mustCreateClient(svc.currencySvcAddr, "currency")
	svc.currencySvcClient = pb.NewCurrencyServiceClient(c)
	defer c.Close()

	mustMapEnv(&svc.emailSvcAddr, "EMAIL_ADDR")
	c = mustCreateClient(svc.emailSvcAddr, "email")
	svc.emailSvcClient = pb.NewEmailServiceClient(c)
	defer c.Close()

	mustMapEnv(&svc.paymentSvcAddr, "PAYMENT_ADDR")
	c = mustCreateClient(svc.paymentSvcAddr, "payment")
	svc.paymentSvcClient = pb.NewPaymentServiceClient(c)
	defer c.Close()

	svc.kafkaBrokerSvcAddr = os.Getenv("KAFKA_ADDR")

	if svc.kafkaBrokerSvcAddr != "" {
		svc.KafkaProducerClient, err = kafka.CreateKafkaProducer([]string{svc.kafkaBrokerSvcAddr}, logger)
		if err != nil {
			logger.Error(err.Error())
		}
	}

	logger.Info(fmt.Sprintf("service config: %+v", svc))

	// Load gRPC server TLS configuration
	serverTLSConfig := ServerTLSConfig{
		Enabled:            os.Getenv("GRPC_SERVER_TLS_ENABLED") == "true",
		CertPath:           os.Getenv("GRPC_SERVER_TLS_CERT_PATH"),
		KeyPath:            os.Getenv("GRPC_SERVER_TLS_KEY_PATH"),
		ClientAuthRequired: os.Getenv("GRPC_SERVER_TLS_CLIENT_AUTH_REQUIRED") == "true",
		CACertPath:         os.Getenv("GRPC_SERVER_TLS_CA_CERT_PATH"),
	}

	lis, err := net.Listen("tcp", fmt.Sprintf(":%s", port))
	if err != nil {
		logger.Error(err.Error())
	}

	srv, err := NewGRPCServer(serverTLSConfig,
		grpc.StatsHandler(otelgrpc.NewServerHandler()),
	)
	if err != nil {
		logger.Error(fmt.Sprintf("failed to create gRPC server: %v", err))
		os.Exit(1)
	}
	pb.RegisterCheckoutServiceServer(srv, svc)

	healthpb.RegisterHealthServer(srv, healthcheck)
	// Mark service as serving once all initialization is complete
	healthcheck.SetServingStatus("", healthpb.HealthCheckResponse_SERVING)

	// Start HTTP server in goroutine
	go func() {
		logger.Info(fmt.Sprintf("HTTP health endpoints listening on port %s", httpPort))
		if err := httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			logger.Error(fmt.Sprintf("HTTP server failed: %v", err))
		}
	}()

	logger.Info(fmt.Sprintf("starting to listen on tcp: %q", lis.Addr().String()))
	logger.Info(fmt.Sprintf("Checkout service started on port %s", port))
	err = srv.Serve(lis)
	logger.Error(err.Error())

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM, syscall.SIGKILL)
	defer cancel()

	go func() {
		if err := srv.Serve(lis); err != nil {
			logger.Error(err.Error())
		}
	}()

	<-ctx.Done()

	srv.GracefulStop()
	logger.Info("Checkout gRPC server stopped")

	// Shutdown HTTP server gracefully
	ctxShutdown, cancelShutdown := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancelShutdown()
	if err := httpServer.Shutdown(ctxShutdown); err != nil {
		logger.Error(fmt.Sprintf("HTTP server shutdown failed: %v", err))
	}
	logger.Info("Checkout HTTP server stopped")
}

func mustMapEnv(target *string, envKey string) {
	v := os.Getenv(envKey)
	if v == "" {
		panic(fmt.Sprintf("environment variable %q not set", envKey))
	}
	*target = v
}

func (cs *checkout) Check(ctx context.Context, req *healthpb.HealthCheckRequest) (*healthpb.HealthCheckResponse, error) {
	return &healthpb.HealthCheckResponse{Status: healthpb.HealthCheckResponse_SERVING}, nil
}

func (cs *checkout) Watch(req *healthpb.HealthCheckRequest, ws healthpb.Health_WatchServer) error {
	return status.Errorf(codes.Unimplemented, "health check via Watch not implemented")
}

func (cs *checkout) PlaceOrder(ctx context.Context, req *pb.PlaceOrderRequest) (*pb.PlaceOrderResponse, error) {
	span := trace.SpanFromContext(ctx)
	span.SetAttributes(
		attribute.String("user.id", req.UserId),
		attribute.String("demo.user_context.selected_currency", req.UserCurrency),
	)
	logger.LogAttrs(
		ctx,
		slog.LevelInfo, "[PlaceOrder]",
		slog.String("user_id", req.UserId),
		slog.String("user_currency", req.UserCurrency),
	)

	// Validate request before processing
	if err := ValidatePlaceOrderRequest(req); err != nil {
		return nil, err
	}

	var err error
	defer func() {
		if err != nil {
			span.RecordError(err)
		}
	}()

	orderID, err := uuid.NewUUID()
	if err != nil {
		return nil, status.Errorf(codes.Internal, "failed to generate order uuid")
	}

	// Generate idempotency key for this checkout request
	idempotencyKey := GenerateIdempotencyKey()
	
	// Add idempotency key to context for downstream gRPC calls
	md := metadata.New(nil)
	md.Set("idempotency-key", idempotencyKey)
	ctxWithIdempotency := metadata.NewOutgoingContext(ctx, md)

	prep, err := cs.prepareOrderItemsAndShippingQuoteFromCart(ctx, req.UserId, req.UserCurrency, req.Address)
	if err != nil {
		return nil, status.Error(codes.Internal, err.Error())
	}
	span.AddEvent("prepared")

	total := &pb.Money{
		CurrencyCode: req.UserCurrency,
		Units:        0,
		Nanos:        0,
	}
	total = money.Must(money.Sum(total, prep.shippingCostLocalized))
	for _, it := range prep.orderItems {
		multPrice := money.MultiplySlow(it.Cost, uint32(it.GetItem().GetQuantity()))
		total = money.Must(money.Sum(total, multPrice))
	}

	txID, err := cs.chargeCard(ctxWithIdempotency, total, req.CreditCard)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "failed to charge card: %+v", err)
	}

	span.AddEvent("charged",
		trace.WithAttributes(attribute.String("demo.payment.transaction.id", txID)))
	logger.LogAttrs(
		ctx,
		slog.LevelInfo, "payment went through",
		slog.String("transaction_id", txID),
	)

	shippingTrackingID, err := cs.shipOrder(ctx, req.Address, prep.cartItems, idempotencyKey)
	if err != nil {
		return nil, status.Errorf(codes.Unavailable, "shipping error: %+v", err)
	}
	shippingTrackingAttribute := attribute.String("demo.shipping.tracking.id", shippingTrackingID)
	span.AddEvent("shipped", trace.WithAttributes(shippingTrackingAttribute))

	_ = cs.emptyUserCart(ctx, req.UserId)

	orderResult := &pb.OrderResult{
		OrderId:            orderID.String(),
		ShippingTrackingId: shippingTrackingID,
		ShippingCost:       prep.shippingCostLocalized,
		ShippingAddress:    req.Address,
		Items:              prep.orderItems,
	}

	shippingCostFloat, _ := strconv.ParseFloat(fmt.Sprintf("%d.%02d", prep.shippingCostLocalized.GetUnits(), prep.shippingCostLocalized.GetNanos()/1000000000), 64)
	totalPriceFloat, _ := strconv.ParseFloat(fmt.Sprintf("%d.%02d", total.GetUnits(), total.GetNanos()/1000000000), 64)

	span.SetAttributes(
		attribute.String("demo.order.id", orderID.String()),
		attribute.Float64("demo.shipping.amount", shippingCostFloat),
		attribute.Float64("demo.order.amount", totalPriceFloat),
		attribute.Int("demo.order.items.count", len(prep.orderItems)),
		shippingTrackingAttribute,
	)
	logger.LogAttrs(
		ctx,
		slog.LevelInfo, "order placed",
		slog.String("demo.order.id", orderID.String()),
		slog.Float64("demo.shipping.amount", shippingCostFloat),
		slog.Float64("demo.order.amount", totalPriceFloat),
		slog.Int("demo.order.items.count", len(prep.orderItems)),
		slog.String("demo.shipping.tracking.id", shippingTrackingID),
	)

	if err := cs.sendOrderConfirmation(ctx, req.Email, orderResult); err != nil {
		logger.Warn(fmt.Sprintf("failed to send order confirmation: %+v", err))
	} else {
		logger.Info("order confirmation email sent")
	}

	// send to kafka only if kafka broker address is set
	if cs.kafkaBrokerSvcAddr != "" {
		logger.Info("sending to postProcessor")
		cs.sendToPostProcessor(ctx, orderResult)
	}

	resp := &pb.PlaceOrderResponse{Order: orderResult}
	return resp, nil
}

type orderPrep struct {
	orderItems            []*pb.OrderItem
	cartItems             []*pb.CartItem
	shippingCostLocalized *pb.Money
}

func (cs *checkout) prepareOrderItemsAndShippingQuoteFromCart(ctx context.Context, userID, userCurrency string, address *pb.Address) (orderPrep, error) {
	ctx, span := tracer.Start(ctx, "prepareOrderItemsAndShippingQuoteFromCart")
	defer span.End()

	var out orderPrep
	cartItems, err := cs.getUserCart(ctx, userID)
	if err != nil {
		return out, fmt.Errorf("cart failure: %+v", err)
	}
	orderItems, err := cs.prepOrderItems(ctx, cartItems, userCurrency)
	if err != nil {
		return out, fmt.Errorf("failed to prepare order: %+v", err)
	}
	shippingUSD, err := cs.quoteShipping(ctx, address, cartItems)
	if err != nil {
		return out, fmt.Errorf("shipping quote failure: %+v", err)
	}
	shippingPrice, err := cs.convertCurrency(ctx, shippingUSD, userCurrency)
	if err != nil {
		return out, fmt.Errorf("failed to convert shipping cost to currency: %+v", err)
	}

	out.shippingCostLocalized = shippingPrice
	out.cartItems = cartItems
	out.orderItems = orderItems

	var totalCart int32
	for _, ci := range cartItems {
		totalCart += ci.Quantity
	}
	shippingCostFloat, _ := strconv.ParseFloat(fmt.Sprintf("%d.%02d", shippingPrice.GetUnits(), shippingPrice.GetNanos()/1000000000), 64)

	span.SetAttributes(


// mustCreateClient creates a gRPC client for the given target address with retry and circuit breaker interceptors
func mustCreateClient(addr string, svcName string) *grpc.ClientConn {
	// Configure retry interceptor
	retryCodes := []codes.Code{codes.Unavailable, codes.ResourceExhausted, codes.Aborted}
	retryInterceptor := NewRetryInterceptor(maxRetryAttempts, initialBackoff, retryCodes, retryMetrics)

	// Load client TLS configuration for this service
	svcNameUpper := strings.ToUpper(svcName)
	clientTLSConfig := ClientTLSConfig{
		Enabled:        os.Getenv(fmt.Sprintf("%s_GRPC_CLIENT_TLS_ENABLED", svcNameUpper)) == "true",
		CACertPath:     os.Getenv(fmt.Sprintf("%s_GRPC_CLIENT_TLS_CA_CERT_PATH", svcNameUpper)),
		ClientCertPath: os.Getenv(fmt.Sprintf("%s_GRPC_CLIENT_TLS_CLIENT_CERT_PATH", svcNameUpper)),
		ClientKeyPath:  os.Getenv(fmt.Sprintf("%s_GRPC_CLIENT_TLS_CLIENT_KEY_PATH", svcNameUpper)),
	}

	c, err := NewGRPCClientConn(addr, clientTLSConfig,
		grpc.WithStatsHandler(otelgrpc.NewClientHandler()),
		grpc.WithUnaryInterceptor(retryInterceptor),
		grpc.WithUnaryInterceptor(circuitBreakerUnaryInterceptor(svcName)),
	)
	if err != nil {
		logger.Error(fmt.Sprintf("could not connect to %s service, err: %+v", svcName, err))
		panic(err)
	}

	return c
}

func (cs *checkout) quoteShipping(ctx context.Context, address *pb.Address, items []*pb.CartItem) (*pb.Money, error) {
	quotePayload, err := json.Marshal(map[string]interface{}{
		"address": address,
		"items":   items,
	})
	if err != nil {
		return nil, fmt.Errorf("failed to marshal ship order request: %+v", err)
	}

	req, err := http.NewRequestWithContext(ctx, "POST", cs.shippingSvcAddr+"/get-quote", bytes.NewBuffer(quotePayload))
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %+v", err)
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := cs.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("failed POST to shipping service: %+v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("failed POST to email service: expected 200, got %d", resp.StatusCode)
	}

	shippingQuoteBytes, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read shipping quote response: %+v", err)
	}

	var quoteResp struct {
		CostUsd *pb.Money `json:"cost_usd"`
	}
	if err := json.Unmarshal(shippingQuoteBytes, &quoteResp); err != nil {
		return nil, fmt.Errorf("failed to unmarshal shipping quote: %+v", err)
	}
	if quoteResp.CostUsd == nil {
		return nil, fmt.Errorf("shipping quote missing cost_usd field")
	}

	return quoteResp.CostUsd, nil
}

func (cs *checkout) getUserCart(ctx context.Context, userID string) ([]*pb.CartItem, error) {
	cart, err := cs.cartSvcClient.GetCart(ctx, &pb.GetCartRequest{UserId: userID})
	if err != nil {
		return nil, fmt.Errorf("failed to get user cart during checkout: %+v", err)
	}
	return cart.GetItems(), nil
}

func (cs *checkout) emptyUserCart(ctx context.Context, userID string) error {
	if _, err := cs.cartSvcClient.EmptyCart(ctx, &pb.EmptyCartRequest{UserId: userID}); err != nil {
		return fmt.Errorf("failed to empty user cart during checkout: %+v", err)
	}
	return nil
}

func (cs *checkout) prepOrderItems(ctx context.Context, items []*pb.CartItem, userCurrency string) ([]*pb.OrderItem, error) {
	out := make([]*pb.OrderItem, len(items))

	for i, item := range items {
		product, err := cs.productCatalogSvcClient.GetProduct(ctx, &pb.GetProductRequest{Id: item.GetProductId()})
		if err != nil {
			return nil, fmt.Errorf("failed to get product #%q", item.GetProductId())
		}
		price, err := cs.convertCurrency(ctx, product.GetPriceUsd(), userCurrency)
		if err != nil {
			return nil, fmt.Errorf("failed to convert price of %q to %s", item.GetProductId(), userCurrency)
		}
		out[i] = &pb.OrderItem{
			Item: item,
			Cost: price,
		}
	}
	return out, nil
}

func (cs *checkout) convertCurrency(ctx context.Context, from *pb.Money, toCurrency string) (*pb.Money, error) {
	result, err := cs.currencySvcClient.Convert(ctx, &pb.CurrencyConversionRequest{
		From:   from,
		ToCode: toCurrency,
	})
	if err != nil {
		return nil, fmt.Errorf("failed to convert currency: %+v", err)
	}
	return result, err
}

func (cs *checkout) chargeCard(ctx context.Context, amount *pb.Money, paymentInfo *pb.CreditCardInfo) (string, error) {
	paymentService := cs.paymentSvcClient
	if flags.PaymentUnreachable.Value(ctx, openfeature.EvaluationContext{}) {
		badAddress := "badAddress:50051"
		c := mustCreateClient(badAddress, "payment")
		paymentService = pb.NewPaymentServiceClient(c)
	}

	paymentResp, err := paymentService.Charge(ctx, &pb.ChargeRequest{
		Amount:     amount,
		CreditCard: paymentInfo,
	})
	if err != nil {
		return "", fmt.Errorf("could not charge the card: %+v", err)
	}
	return paymentResp.GetTransactionId(), nil
}

func (cs *checkout) sendOrderConfirmation(ctx context.Context, email string, order *pb.OrderResult) error {
	emailPayload, err := json.Marshal(map[string]interface{}{
		"email": email,
		"order": order,
	})
	if err != nil {
		return fmt.Errorf("failed to marshal order to JSON: %+v", err)
	}

	req, err := http.NewRequestWithContext(ctx, "POST", cs.emailSvcAddr+"/send_order_confirmation", bytes.NewBuffer(emailPayload))
	if err != nil {
		return fmt.Errorf("failed to create request: %+v", err)
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := cs.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("failed POST to email service: %+v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("failed POST to email service: expected 200, got %d", resp.StatusCode)
	}

	return err
}

func (cs *checkout) shipOrder(ctx context.Context, address *pb.Address, items []*pb.CartItem, idempotencyKey string) (string, error) {
	shipPayload, err := json.Marshal(map[string]interface{}{
		"address": address,
		"items":   items,
	})
	if err != nil {
		return "", fmt.Errorf("failed to marshal ship order request: %+v", err)
	}

	req, err := http.NewRequestWithContext(ctx, "POST", cs.shippingSvcAddr+"/ship-order", bytes.NewBuffer(shipPayload))
	if err != nil {
		return "", fmt.Errorf("failed to create request: %+v", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Idempotency-Key", idempotencyKey)
	resp, err := cs.httpClient.Do(req)
	if err != nil {
		return "", fmt.Errorf("failed POST to shipping service: %+v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("failed POST to shipping service: expected 200, got %d", resp.StatusCode)
	}

	trackingRespBytes, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", fmt.Errorf("failed to read ship order response: %+v", err)
	}

	var shipResp struct {
		TrackingID string `json:"tracking_id"`
	}
	if err := json.Unmarshal(trackingRespBytes, &shipResp); err != nil {
		return "", fmt.Errorf("failed to unmarshal ship order response: %+v", err)
	}
	if shipResp.TrackingID == "" {
		return "", fmt.Errorf("ship order response missing tracking_id field")
	}

	return shipResp.TrackingID, nil
}

func (cs *checkout) sendToPostProcessor(ctx context.Context, result *pb.OrderResult) {
	message, err := proto.Marshal(result)
	if err != nil {
		logger.Error(fmt.Sprintf("Failed to marshal message to protobuf: %+v", err))
		return
	}

	msg := sarama.ProducerMessage{
		Topic: kafka.Topic,
		Value: sarama.ByteEncoder(message),
	}

	// Inject tracing info into message
	span := createProducerSpan(ctx, &msg)
	defer span.End()

	// Send message and handle response
	startTime := time.Now()
	select {
	case cs.KafkaProducerClient.Input() <- &msg:
		select {
		case successMsg := <-cs.KafkaProducerClient.Successes():
			span.SetAttributes(
				attribute.Bool("messaging.kafka.producer.success", true),
				attribute.Int("messaging.kafka.producer.duration_ms", int(time.Since(startTime).Milliseconds())),
				attribute.KeyValue(semconv.MessagingKafkaMessageOffset(int(successMsg.Offset))),
			)
			logger.Info(fmt.Sprintf("Successful to write message. offset: %v, duration: %v", successMsg.Offset, time.Since(startTime)))
		case errMsg := <-cs.KafkaProducerClient.Errors():
			span.SetAttributes(
				attribute.Bool("messaging.kafka.producer.success", false),
				attribute.Int("messaging.kafka.producer.duration_ms", int(time.Since(startTime).Milliseconds())),
			)
			span.SetStatus(otelcodes.Error, errMsg.Err.Error())
			logger.Error(fmt.Sprintf("Failed to write message: %v", errMsg.Err))
		case <-ctx.Done():
			span.SetAttributes(
				attribute.Bool("messaging.kafka.producer.success", false),
				attribute.Int("messaging.kafka.producer.duration_ms", int(time.Since(startTime).Milliseconds())),
			)
			span.SetStatus(otelcodes.Error, "Context cancelled: "+ctx.Err().Error())
			logger.Warn(fmt.Sprintf("Context canceled before success message received: %v", ctx.Err()))
		}
	case <-ctx.Done():
		span.SetAttributes(
			attribute.Bool("messaging.kafka.producer.success", false),
			attribute.Int("messaging.kafka.producer.duration_ms", int(time.Since(startTime).Milliseconds())),
		)
		span.SetStatus(otelcodes.Error, "Failed to send: "+ctx.Err().Error())
		logger.Error(fmt.Sprintf("Failed to send message to Kafka within context deadline: %v", ctx.Err()))
		return
	}

	ffValue := flags.KafkaQueueProblems.Value(ctx, openfeature.EvaluationContext{})
	if ffValue > 0 {
		logger.Info("Warning: FeatureFlag 'kafkaQueueProblems' is activated, overloading queue now.")
		for range ffValue {
			go func(msg sarama.ProducerMessage) {
				cs.KafkaProducerClient.Input() <- &msg
				<-cs.KafkaProducerClient.Successes()
			}(msg)
		}
		logger.Info(fmt.Sprintf("Done with #%d messages for overload simulation.", ffValue))
	}
}

func createProducerSpan(ctx context.Context, msg *sarama.ProducerMessage) trace.Span {
	spanContext, span := tracer.Start(
		ctx,
		fmt.Sprintf("%s publish", msg.Topic),
		trace.WithSpanKind(trace.SpanKindProducer),
		trace.WithAttributes(
			semconv.PeerService("kafka"),
			semconv.NetworkTransportTCP,
			semconv.MessagingSystemKafka,
			semconv.MessagingDestinationName(msg.Topic),
			semconv.MessagingOperationPublish,
			semconv.MessagingKafkaDestinationPartition(int(msg.Partition)),
		),
	)

	carrier := propagation.MapCarrier{}
	propagator := otel.GetTextMapPropagator()
	propagator.Inject(spanContext, carrier)

	for key, value := range carrier {
		msg.Headers = append(msg.Headers, sarama.RecordHeader{Key: []byte(key), Value: []byte(value)})
	}

	return span
}
