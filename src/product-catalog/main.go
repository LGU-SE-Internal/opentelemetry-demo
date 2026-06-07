// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
package main

//go:generate go install google.golang.org/protobuf/cmd/protoc-gen-go
//go:generate go install google.golang.org/grpc/cmd/protoc-gen-go-grpc
//go:generate protoc --go_out=./ --go-grpc_out=./ --proto_path=../../pb ../../pb/demo.proto
//go:generate go install github.com/open-feature/cli/cmd/openfeature@v0.4.0
//go:generate openfeature generate -o flags --package-name flags go

import (
	"context"
	"database/sql"
	"fmt"
	"log/slog"
	"net"
	"os"
	"os/signal"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	_ "github.com/lib/pq"
	"go.opentelemetry.io/contrib/bridges/otelslog"
	"go.opentelemetry.io/contrib/instrumentation/google.golang.org/grpc/otelgrpc"
	"go.opentelemetry.io/contrib/instrumentation/runtime"
	"go.opentelemetry.io/contrib/otelconf"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	otelcodes "go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/log/global"
	"go.opentelemetry.io/otel/metric"
	semconv "go.opentelemetry.io/otel/semconv/v1.38.0"
	"go.opentelemetry.io/otel/trace"
	"golang.org/x/time/rate"

	otelhooks "github.com/open-feature/go-sdk-contrib/hooks/open-telemetry/pkg"
	flagd "github.com/open-feature/go-sdk-contrib/providers/flagd/pkg"
	"github.com/open-feature/go-sdk/openfeature"
	pb "github.com/open-telemetry/opentelemetry-demo/src/product-catalog/genproto/oteldemo"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/health"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/peer"
	"google.golang.org/grpc/reflection"
	"google.golang.org/grpc/status"

	"github.com/XSAM/otelsql"
	flags "github.com/opentelemetry/opentelemetry-demo/src/product-catalog/flags"
)

var (
	logger *slog.Logger
	db     *sql.DB
	reg    metric.Registration
	alphanumericRegex = regexp.MustCompile(`^[a-zA-Z0-9]*$`)
)

func validateGetProductRequest(req *pb.GetProductRequest) error {
	if req.Id == "" {
		return status.Error(codes.InvalidArgument, "id must not be empty")
	}
	if len(req.Id) > 64 {
		return status.Errorf(codes.InvalidArgument, "id must not exceed 64 characters, got %d", len(req.Id))
	}
	if !alphanumericRegex.MatchString(req.Id) {
		return status.Error(codes.InvalidArgument, "id must only contain alphanumeric characters")
	}
	return nil
}

func validateListProductsRequest(req *pb.ListProductsRequest) error {
	if req.PageSize < 1 || req.PageSize > 100 {
		return status.Errorf(codes.InvalidArgument, "page_size must be between 1 and 100 inclusive, got %d", req.PageSize)
	}
	if req.PageToken < 1 {
		return status.Errorf(codes.InvalidArgument, "page_token must be >= 1, got %d", req.PageToken)
	}
	return nil
}

func validateSearchProductsRequest(req *pb.SearchProductsRequest) error {
	if len(req.Query) > 256 {
		return status.Errorf(codes.InvalidArgument, "query must not exceed 256 characters, got %d", len(req.Query))
	}
	if req.PageSize < 1 || req.PageSize > 100 {
		return status.Errorf(codes.InvalidArgument, "page_size must be between 1 and 100 inclusive, got %d", req.PageSize)
	}
	if req.PageToken < 1 {
		return status.Errorf(codes.InvalidArgument, "page_token must be >= 1, got %d", req.PageToken)
	}
	return nil
}

type clientLimiter struct {
	limiter  *rate.Limiter
	lastSeen time.Time
}

var (
	limiters sync.Map
)

func RateLimitInterceptor(rateLimitRPS float64, rateLimitCounter metric.Int64Counter) grpc.UnaryServerInterceptor {
	// Start cleanup goroutine for stale limiters
	go func() {
		ticker := time.NewTicker(5 * time.Minute)
		defer ticker.Stop()
		for range ticker.C {
			limiters.Range(func(key, value interface{}) bool {
				ip := key.(string)
				cl := value.(*clientLimiter)
				if time.Since(cl.lastSeen) > 1*time.Hour {
					limiters.Delete(ip)
				}
				return true
			})
		}
	}()

	return func(ctx context.Context, req interface{}, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (interface{}, error) {
		// Extract client IP
		clientIP := "unknown"
		if md, ok := metadata.FromIncomingContext(ctx); ok {
			if xff := md.Get("x-forwarded-for"); len(xff) > 0 {
				parts := strings.Split(xff[0], ",")
				if len(parts) > 0 {
					clientIP = strings.TrimSpace(parts[0])
				}
			}
		}
		if clientIP == "unknown" {
			if p, ok := peer.FromContext(ctx); ok {
				if addr, ok := p.Addr.(*net.TCPAddr); ok {
					clientIP = addr.IP.String()
				}
			}
		}

		// Get or create limiter for client IP
		limiterValue, ok := limiters.Load(clientIP)
		if !ok {
			limiter := rate.NewLimiter(rate.Limit(rateLimitRPS), int(rateLimitRPS))
			limiterValue = &clientLimiter{
				limiter:  limiter,
				lastSeen: time.Now(),
			}
			limiters.Store(clientIP, limiterValue)
		}
		cl := limiterValue.(*clientLimiter)
		cl.lastSeen = time.Now()

		// Check rate limit
		if !cl.limiter.Allow() {
			// Increment rate limit counter
			rateLimitCounter.Add(ctx, 1,
				metric.WithAttributes(
					attribute.String("client_ip", clientIP),
					attribute.String("grpc_method", info.FullMethod),
				),
			)
			return nil, status.Error(codes.ResourceExhausted, "rate limit exceeded, please try again later")
		}

		// Call handler
		return handler(ctx, req)
	}
}

type productCatalog struct {
	pb.UnimplementedProductCatalogServiceServer
}

func init() {
	logger = otelslog.NewLogger("product-catalog")
}

func initDatabase() error {
	connStr := os.Getenv("DB_CONNECTION_STRING")
	if connStr == "" {
		return fmt.Errorf("DB_CONNECTION_STRING environment variable not set")
	}

	dbAttrs := otelsql.WithAttributes(
		append(otelsql.AttributesFromDSN(connStr), semconv.DBSystemNamePostgreSQL)...,
	)

	var err error
	db, err = otelsql.Open("postgres", connStr,
		dbAttrs,
		otelsql.WithSpanOptions(otelsql.SpanOptions{
			OmitConnResetSession: true,
			OmitRows:             true,
		}))
	if err != nil {
		return fmt.Errorf("failed to open database connection: %w", err)
	}

	reg, err = otelsql.RegisterDBStatsMetrics(db, dbAttrs)
	if err != nil {
		return fmt.Errorf("failed to register database metrics: %w", err)
	}

	// Test the connection
	if err := db.Ping(); err != nil {
		return fmt.Errorf("failed to ping database: %w", err)
	}

	logger.Info("Database connection established")
	return nil
}

func main() {
	ctx := context.Background()

	// Initialize OpenTelemetry SDK with otelconf
	sdk, err := otelconf.NewSDK(otelconf.WithContext(ctx))
	if err != nil {
		logger.Error(fmt.Sprintf("Failed to initialize OpenTelemetry SDK: %v", err))
		os.Exit(1)
	}
	defer func() {
		if err := sdk.Shutdown(ctx); err != nil {
			logger.Error(fmt.Sprintf("Error shutting down OpenTelemetry SDK: %v", err))
		}
		logger.Info("Shutdown OpenTelemetry SDK")
	}()

	// Set global providers and propagator
	otel.SetTracerProvider(sdk.TracerProvider())
	otel.SetMeterProvider(sdk.MeterProvider())
	global.SetLoggerProvider(sdk.LoggerProvider())
	otel.SetTextMapPropagator(sdk.Propagator())

	// Initialize database connection
	if err := initDatabase(); err != nil {
		logger.Error(fmt.Sprintf("Error initializing database: %v", err))
		os.Exit(1)
	}
	defer func() {
		if db != nil {
			if err := db.Close(); err != nil {
				logger.Error(fmt.Sprintf("Error closing database connection: %v", err))
			} else {
				logger.Info("Database connection closed")
			}
		}
		if reg != nil {
			if err := reg.Unregister(); err != nil {
				logger.Error(fmt.Sprintf("Error unregistering database metrics: %v", err))
			} else {
				logger.Info("Database metrics unregistered")
			}
		}
	}()

	openfeature.AddHooks(otelhooks.NewTracesHook())
	provider, err := flagd.NewProvider()
	if err != nil {
		logger.Error("Error creating flagd provider", slog.Any("error", err))
	}

	err = openfeature.SetProvider(provider)
	if err != nil {
		logger.Error("Failed to set flagd as the provider", slog.Any("error", err))
	}
	defer openfeature.Shutdown()

	err = runtime.Start(runtime.WithMinimumReadMemStatsInterval(time.Second))
	if err != nil {
		logger.Error(err.Error())
	}

	// Initialize rate limit
	rateLimitRPS := 100.0
	if rateLimitStr := os.Getenv("PRODUCT_CATALOG_RATE_LIMIT_RPS"); rateLimitStr != "" {
		if parsedRate, err := strconv.ParseFloat(rateLimitStr, 64); err == nil && parsedRate > 0 {
			rateLimitRPS = parsedRate
		} else {
			logger.Warn("Invalid PRODUCT_CATALOG_RATE_LIMIT_RPS value, using default 100.0", slog.Any("error", err))
		}
	}

	meter := otel.Meter("product-catalog")
	rateLimitCounter, err := meter.Int64Counter(
		"otel_demo_product_catalog_rate_limited_requests_total",
		metric.WithDescription("Total number of requests that were rate limited"),
	)
	if err != nil {
		logger.Error("Failed to create rate limit counter metric", slog.Any("error", err))
		os.Exit(1)
	}

	svc := &productCatalog{}
	var port string
	mustMapEnv(&port, "PRODUCT_CATALOG_PORT")

	logger.Info(fmt.Sprintf("Product Catalog gRPC server started on port: %s", port))

	ln, err := net.Listen("tcp", fmt.Sprintf(":%s", port))
	if err != nil {
		logger.Error(fmt.Sprintf("TCP Listen: %v", err))
	}

	srv := grpc.NewServer(
		grpc.StatsHandler(otelgrpc.NewServerHandler()),
		grpc.UnaryInterceptor(RateLimitInterceptor(rateLimitRPS, rateLimitCounter)),
	)

	reflection.Register(srv)

	pb.RegisterProductCatalogServiceServer(srv, svc)

	healthcheck := health.NewServer()
	healthpb.RegisterHealthServer(srv, healthcheck)

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM, syscall.SIGKILL)
	defer cancel()

	go func() {
		if err := srv.Serve(ln); err != nil {
			logger.Error(fmt.Sprintf("Failed to serve gRPC server, err: %v", err))
		}
	}()

	<-ctx.Done()

	srv.GracefulStop()
	logger.Info("Product Catalog gRPC server stopped")
}

func loadProductsFromDB(ctx context.Context) ([]*pb.Product, error) {
	if db == nil {
		return nil, fmt.Errorf("database connection not initialized")
	}

	// Query all products with categories
	rows, err := db.QueryContext(ctx, `
		SELECT p.id, p.name, p.description, p.picture, 
		       p.price_currency_code, p.price_units, p.price_nanos, p.categories
		FROM catalog.products p
		ORDER BY p.id
	`)
	if err != nil {
		return nil, fmt.Errorf("failed to query products: %w", err)
	}
	defer rows.Close()

	products, err := getProductsFromRows(ctx, rows)
	if err != nil {
		return nil, fmt.Errorf("failed to get products from rows: %w", err)
	}

	return products, nil
}

func searchProductsFromDB(ctx context.Context, query string) ([]*pb.Product, error) {
	if db == nil {
		return nil, fmt.Errorf("database connection not initialized")
	}

	// Query products matching search query in name or description
	searchPattern := "%" + strings.ToLower(query) + "%"
	rows, err := db.QueryContext(ctx, `
		SELECT p.id, p.name, p.description, p.picture, 
		       p.price_currency_code, p.price_units, p.price_nanos, p.categories
		FROM catalog.products p
		WHERE LOWER(p.name) LIKE $1 OR LOWER(p.description) LIKE $1
		ORDER BY p.id
	`, searchPattern)
	if err != nil {
		return nil, fmt.Errorf("failed to query products: %w", err)
	}
	defer rows.Close()

	products, err := getProductsFromRows(ctx, rows)
	if err != nil {
		return nil, fmt.Errorf("failed to get products from rows: %w", err)
	}

	return products, nil
}

func getProductFromDB(ctx context.Context, productID string) (*pb.Product, error) {
	if db == nil {
		return nil, fmt.Errorf("database connection not initialized")
	}

	// Query single product by ID
	row := db.QueryRowContext(ctx, `
		SELECT p.id, p.name, p.description, p.picture, 
		       p.price_currency_code, p.price_units, p.price_nanos, p.categories
		FROM catalog.products p
		WHERE p.id = $1
	`, productID)

	var id, name, description, picture, currencyCode, categoriesStr string
	var units int64
	var nanos int32

	if err := row.Scan(&id, &name, &description, &picture, &currencyCode, &units, &nanos, &categoriesStr); err != nil {
		if err == sql.ErrNoRows {
			return nil, fmt.Errorf("product not found")
		}
		return nil, fmt.Errorf("failed to scan product row: %w", err)
	}

	return parseProductRow(id, name, description, picture, currencyCode, categoriesStr, units, nanos), nil
}

func getProductsFromRows(ctx context.Context, rows *sql.Rows) ([]*pb.Product, error) {
	var products []*pb.Product

	for rows.Next() {
		var id, name, description, picture, currencyCode, categoriesStr string
		var units int64
		var nanos int32

		if err := rows.Scan(&id, &name, &description, &picture, &currencyCode, &units, &nanos, &categoriesStr); err != nil {
			return nil, fmt.Errorf("failed to scan product row: %w", err)
		}

		products = append(products, parseProductRow(id, name, description, picture, currencyCode, categoriesStr, units, nanos))
	}

	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("error iterating product rows: %w", err)
	}

	logger.LogAttrs(
		ctx,
		slog.LevelInfo,
		fmt.Sprintf("Found %d products from database", len(products)),
		slog.Int("products", len(products)),
	)

	return products, nil
}

func parseProductRow(id, name, description, picture, currencyCode, categoriesStr string, units int64, nanos int32) *pb.Product {
	// Parse comma-delimited categories string into slice
	var categories []string
	if categoriesStr != "" {
		categories = strings.Split(categoriesStr, ",")
		// Trim whitespace from each category
		for i, cat := range categories {
			categories[i] = strings.TrimSpace(cat)
		}
	}

	return &pb.Product{
		Id:          id,
		Name:        name,
		Description: description,
		Picture:     picture,
		PriceUsd: &pb.Money{
			CurrencyCode: currencyCode,
			Units:        units,
			Nanos:        nanos,
		},
		Categories: categories,
	}
}

func mustMapEnv(target *string, key string) {
	value, present := os.LookupEnv(key)
	if !present {
		logger.Error(fmt.Sprintf("Environment Variable Not Set: %q", key))
	}
	*target = value
}

func (p *productCatalog) Check(ctx context.Context, req *healthpb.HealthCheckRequest) (*healthpb.HealthCheckResponse, error) {
	return &healthpb.HealthCheckResponse{Status: healthpb.HealthCheckResponse_SERVING}, nil
}

func (p *productCatalog) Watch(req *healthpb.HealthCheckRequest, ws healthpb.Health_WatchServer) error {
	return status.Errorf(codes.Unimplemented, "health check via Watch not implemented")
}

func (p *productCatalog) ListProducts(ctx context.Context, req *pb.ListProductsRequest) (*pb.ListProductsResponse, error) {
	if err := validateListProductsRequest(req); err != nil {
		span := trace.SpanFromContext(ctx)
		span.SetStatus(otelcodes.Error, err.Error())
		return nil, err
	}
	span := trace.SpanFromContext(ctx)

	products, err := loadProductsFromDB(ctx)
	if err != nil {
		span.SetStatus(otelcodes.Error, err.Error())
		return nil, status.Errorf(codes.Internal, "failed to load products: %v", err)
	}

	logger.InfoContext(ctx, "Successfully loaded product catalog", slog.Int("product_count", len(products)))

	span.SetAttributes(
		attribute.Int("demo.product.count", len(products)),
	)
	return &pb.ListProductsResponse{Products: products, NextPageToken: 0}, nil
}

func (p *productCatalog) GetProduct(ctx context.Context, req *pb.GetProductRequest) (*pb.Product, error) {
	if err := validateGetProductRequest(req); err != nil {
		span := trace.SpanFromContext(ctx)
		span.SetStatus(otelcodes.Error, err.Error())
		return nil, err
	}
	span := trace.SpanFromContext(ctx)
	span.SetAttributes(
		attribute.String("demo.product.id", req.Id),
	)

	// GetProduct will fail on a specific product when feature flag is enabled
	if p.checkProductFailure(ctx, req.Id) {
		msg := "Error: Product Catalog Fail Feature Flag Enabled"
		span.SetStatus(otelcodes.Error, msg)
		span.AddEvent(msg)
		return nil, status.Error(codes.Internal, msg)
	}

	found, err := getProductFromDB(ctx, req.Id)
	if err != nil {
		msg := fmt.Sprintf("Product Not Found: %s", req.Id)
		span.SetStatus(otelcodes.Error, msg)
		span.AddEvent(msg)
		return nil, status.Error(codes.NotFound, msg)
	}

	span.AddEvent("Product Found")
	span.SetAttributes(
		attribute.String("demo.product.id", req.Id),
		attribute.String("demo.product.name", found.Name),
	)

	logger.LogAttrs(
		ctx,
		slog.LevelInfo, "Product Found",
		slog.String("demo.product.name", found.Name),
		slog.String("demo.product.id", req.Id),
	)

	return found, nil
}

func (p *productCatalog) SearchProducts(ctx context.Context, req *pb.SearchProductsRequest) (*pb.SearchProductsResponse, error) {
	if err := validateSearchProductsRequest(req); err != nil {
		span := trace.SpanFromContext(ctx)
		span.SetStatus(otelcodes.Error, err.Error())
		return nil, err
	}
	span := trace.SpanFromContext(ctx)

	result, err := searchProductsFromDB(ctx, req.Query)
	if err != nil {
		span.SetStatus(otelcodes.Error, err.Error())
		return nil, status.Errorf(codes.Internal, "failed to search products: %v", err)
	}

	span.SetAttributes(
		attribute.Int("demo.product.search.count", len(result)),
	)
	return &pb.SearchProductsResponse{Results: result, NextPageToken: 0}, nil
}

func (p *productCatalog) checkProductFailure(ctx context.Context, id string) bool {
	return flags.ProductCatalogFailure.Value(ctx, openfeature.NewTargetlessEvaluationContext(map[string]any{"product_id": id}))
}
