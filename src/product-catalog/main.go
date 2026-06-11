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
	"crypto/tls"
	"crypto/x509"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"regexp"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"time"

	lru "github.com/hashicorp/golang-lru/v2/expirable"
	"github.com/cenkalti/backoff/v4"
	"github.com/jackc/pgx/v5/pgconn"
	_ "github.com/lib/pq"
	"github.com/sony/gobreaker"
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
	pb "github.com/opentelemetry/opentelemetry-demo/src/product-catalog/genproto/oteldemo"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/health/grpc_health_v1"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/reflection"
	"google.golang.org/grpc/peer"
	"google.golang.org/grpc/status"

	"github.com/XSAM/otelsql"
	flags "github.com/opentelemetry/opentelemetry-demo/src/product-catalog/flags"
)

var (
	logger *slog.Logger
	db     *sql.DB
	reg    metric.Registration
	alphanumericRegex = regexp.MustCompile(`^[a-zA-Z0-9]*$`)
	shutdownInProgress atomic.Bool
	catalogLoaded atomic.Bool
	// TLS errors
	ErrTLSConfigMissingCert = errors.New("TLS config missing certificate path")
	ErrTLSConfigMissingKey  = errors.New("TLS config missing private key path")
	ErrTLSConfigMissingCA   = errors.New("TLS config missing CA bundle path for client auth")
	ErrTLSInvalidCert       = errors.New("invalid TLS certificate/key")
	ErrTLSInvalidCA         = errors.New("invalid CA bundle")
)

// RateLimitConfig defines configuration for rate limiting behavior
type RateLimitConfig struct {
	RPS            float64 // Maximum requests per second per client identifier
	IdentifierType string  // Client identifier type: "ip" or "user_id"
}

// RateLimitInterceptor returns a unary gRPC server interceptor that enforces rate limits per client
func RateLimitInterceptor(config RateLimitConfig) grpc.UnaryServerInterceptor {
	var limiterCache *lru.LRU[string, *rate.Limiter]
	if config.RPS > 0 {
		limiterCache = lru.NewLRU[string, *rate.Limiter](10000, nil, 5*time.Minute)
	}

	return func(ctx context.Context, req interface{}, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (interface{}, error) {
		// Skip rate limiting if disabled
		if config.RPS <= 0 || limiterCache == nil {
			return handler(ctx, req)
		}

		// Extract client identifier
		var clientID string
		switch config.IdentifierType {
		case "user_id":
			if md, ok := metadata.FromIncomingContext(ctx); ok {
				if vals := md.Get("x-user-id"); len(vals) > 0 {
					clientID = vals[0]
				}
			}
			// Fallback to IP if user ID is not present
			if clientID == "" {
				fallthrough
			}
		case "ip":
			fallthrough
		default:
			// Get IP from X-Forwarded-For header first
			if md, ok := metadata.FromIncomingContext(ctx); ok {
				if xff := md.Get("x-forwarded-for"); len(xff) > 0 {
					// Take the first IP in the comma-separated list
					parts := strings.Split(xff[0], ",")
					if len(parts) > 0 {
						clientID = strings.TrimSpace(parts[0])
					}
				}
			}
			// Fallback to peer address if X-Forwarded-For is not present
			if clientID == "" {
				if p, ok := peer.FromContext(ctx); ok {
					if tcpAddr, ok := p.Addr.(*net.TCPAddr); ok {
						clientID = tcpAddr.IP.String()
					} else {
						clientID = p.Addr.String()
					}
				}
			}
		}

		// If we couldn't get any client ID, allow the request
		if clientID == "" {
			return handler(ctx, req)
		}

		// Get or create rate limiter for this client
		limiter, ok := limiterCache.Get(clientID)
		if !ok {
			limiter = rate.NewLimiter(rate.Limit(config.RPS), int(config.RPS)+1)
			limiterCache.Add(clientID, limiter)
		}

		// Check rate limit
		if !limiter.Allow() {
			// Emit structured warn log
			logger.WarnContext(ctx, "rate limit exceeded",
				"client_id", clientID,
				"endpoint", info.FullMethod,
				"rps_limit", config.RPS,
			)

			// Add event to OpenTelemetry span
			if span := trace.SpanFromContext(ctx); span.IsRecording() {
				span.AddEvent("rate_limit_exceeded", trace.WithAttributes(
					attribute.Bool("rate.limit.exceeded", true),
					attribute.Float64("rate.limit.rps", config.RPS),
					attribute.String("rate.limit.client_id", clientID),
				))
			}

			// Return ResourceExhausted status
			return nil, status.Error(codes.ResourceExhausted, "rate limit exceeded")
		}

		return handler(ctx, req)
	}
}

// RetryConfig defines configuration for retry behavior
type RetryConfig struct {
	MaxAttempts       int           // default: 3
	InitialBackoff    time.Duration // default: 100ms
	MaxBackoff        time.Duration // default: 2s
	JitterFactor      float64       // default: 0.2
}

// PostgresRetryMiddleware wraps database operations with retry logic for transient errors
type PostgresRetryMiddleware interface {
	// Execute runs the given operation with retry logic if the operation is marked idempotent
	Execute(ctx context.Context, operationName string, isIdempotent bool, op func() error) error
}

type postgresRetryMiddleware struct {
	config RetryConfig
	tracer trace.Tracer
}

// NewPostgresRetryMiddleware creates a new PostgresRetryMiddleware with the given config
// If config is nil, defaults are used
func NewPostgresRetryMiddleware(config *RetryConfig) PostgresRetryMiddleware {
	cfg := RetryConfig{
		MaxAttempts:    3,
		InitialBackoff: 100 * time.Millisecond,
		MaxBackoff:     2 * time.Second,
		JitterFactor:   0.2,
	}
	if config != nil {
		if config.MaxAttempts > 0 {
			cfg.MaxAttempts = config.MaxAttempts
		}
		if config.InitialBackoff > 0 {
			cfg.InitialBackoff = config.InitialBackoff
		}
		if config.MaxBackoff > 0 {
			cfg.MaxBackoff = config.MaxBackoff
		}
		if config.JitterFactor >= 0 && config.JitterFactor <= 1 {
			cfg.JitterFactor = config.JitterFactor
		}
	}
	return &postgresRetryMiddleware{
		config: cfg,
		tracer: otel.Tracer("product-catalog/retry"),
	}
}

// isTransientPostgresError checks if an error is a transient PostgreSQL error that should be retried
func isTransientPostgresError(err error) bool {
	if err == nil {
		return false
	}

	// Check for context deadline exceeded errors
	if errors.Is(err, context.DeadlineExceeded) {
		return true
	}

	// Check for pgx error codes
	var pgErr *pgconn.PgError
	if errors.As(err, &pgErr) {
		switch pgErr.Code {
		// Lock contention errors
		case "40P01", // deadlock detected
			"55P03": // lock not available
			return true
		// Temporary outages/connection errors
		case "08006", // connection failure
			"08001", // sqlclient unable to establish connection
			"57P01", // admin shutdown
			"57P03": // cannot connect now
			return true
		}
	}

	// Check for connection reset errors
	if strings.Contains(err.Error(), "connection reset by peer") ||
		strings.Contains(err.Error(), "broken pipe") ||
		strings.Contains(err.Error(), "connection refused") {
		return true
	}

	return false
}

// CircuitBreakerConfig defines configuration for circuit breaker behavior
type CircuitBreakerConfig struct {
	FailureThreshold     float64       // default: 0.5 (50%)
	Interval             time.Duration // default: 10s
	Timeout              time.Duration // default: 30s
	HalfOpenMaxRequests  uint32        // default: 1
}

// DBCircuitBreaker wraps sql.DB with circuit breaker functionality
type DBCircuitBreaker struct {
	db                   *sql.DB
	cb                   *gobreaker.CircuitBreaker
	meter                metric.Meter
	retryMiddleware      PostgresRetryMiddleware
	stateTransitionCount metric.Int64Counter
	operationCount       metric.Int64Counter
}
// NewDBCircuitBreaker initializes a new circuit breaker wrapped DB instance
func NewDBCircuitBreaker(db *sql.DB, meter metric.Meter) (*DBCircuitBreaker, error) {
	// Load config from environment variables
	cfg := CircuitBreakerConfig{
		FailureThreshold:    0.5,
		Interval:            10 * time.Second,
		Timeout:             30 * time.Second,
		HalfOpenMaxRequests: 1,
	}

	if val := os.Getenv("PRODUCT_CATALOG_CB_FAILURE_THRESHOLD"); val != "" {
		if f, err := strconv.ParseFloat(val, 64); err == nil && f >= 0.0 && f <= 1.0 {
			cfg.FailureThreshold = f
		}
	}
	if val := os.Getenv("PRODUCT_CATALOG_CB_INTERVAL"); val != "" {
		if d, err := time.ParseDuration(val); err == nil && d > 0 {
			cfg.Interval = d
		}
	}
	if val := os.Getenv("PRODUCT_CATALOG_CB_TIMEOUT"); val != "" {
		if d, err := time.ParseDuration(val); err == nil && d > 0 {
			cfg.Timeout = d
		}
	}
	if val := os.Getenv("PRODUCT_CATALOG_CB_HALF_OPEN_MAX_REQUESTS"); val != "" {
		if i, err := strconv.Atoi(val); err == nil && i > 0 {
			cfg.HalfOpenMaxRequests = uint32(i)
		}
	}

	// Create metrics
	stateTransitionCount, err := meter.Int64Counter(
		"productcatalog.circuit_breaker.state_transitions_total",
		metric.WithDescription("Number of circuit breaker state transitions"),
	)
	if err != nil {
		return nil, fmt.Errorf("failed to create state transition counter: %w", err)
	}

	operationCount, err := meter.Int64Counter(
		"productcatalog.circuit_breaker.operations_total",
		metric.WithDescription("Number of database operations processed by the circuit breaker"),
	)
	if err != nil {
		return nil, fmt.Errorf("failed to create operation counter: %w", err)
	}

	// Create gobreaker settings
	settings := gobreaker.Settings{
		Name:        "product-catalog-postgres",
		MaxRequests: cfg.HalfOpenMaxRequests,
		Interval:    cfg.Interval,
		Timeout:     cfg.Timeout,
		ReadyToTrip: func(counts gobreaker.Counts) bool {
			failureRatio := float64(counts.TotalFailures) / float64(counts.Requests)
			return counts.Requests >= 1 && failureRatio >= cfg.FailureThreshold
		},
		OnStateChange: func(name string, from gobreaker.State, to gobreaker.State) {
			// Emit state transition metric
			stateTransitionCount.Add(context.Background(), 1,
				metric.WithAttributes(attribute.String("state", to.String())),
			)
		},
	}

	cb := gobreaker.NewCircuitBreaker(settings)

	// Initialize default retry middleware
	retryMiddleware := NewPostgresRetryMiddleware(nil)

	return &DBCircuitBreaker{
		db:                   db,
		cb:                   cb,
		meter:                meter,
		retryMiddleware:      retryMiddleware,
		stateTransitionCount: stateTransitionCount,
		operationCount:       operationCount,
	}, nil
}

// execute runs a database operation through the circuit breaker
func (d *DBCircuitBreaker) execute(ctx context.Context, operationName string, isIdempotent bool, op func() error) error {
	state := d.cb.State().String()

	result, err := d.cb.Execute(func() (interface{}, error) {
		// Run the operation with existing retry logic
		err := d.retryMiddleware.Execute(ctx, operationName, isIdempotent, op)
		return nil, err
	})

	// Count operation result
	statusAttr := attribute.String("status", "success")
	if err != nil {
		statusAttr = attribute.String("status", "failure")
		// If circuit is open, return Unavailable error
		if errors.Is(err, gobreaker.ErrOpenState) {
			err = status.Errorf(codes.Unavailable, "product catalog database is temporarily unavailable: %w", err)
		}
	}

	d.operationCount.Add(ctx, 1,
		metric.WithAttributes(statusAttr),
		metric.WithAttributes(attribute.String("state", state)),
	)

	if result != nil {
		return nil
	}
	return err
}

// ListProducts returns all products from the database
func (d *DBCircuitBreaker) ListProducts(ctx context.Context) ([]*pb.Product, error) {
	var products []*pb.Product
	err := d.execute(ctx, "ListProducts", true, func() error {
		rows, err := d.db.QueryContext(ctx, "SELECT id, name, description, picture, price_usd, categories FROM products")
		if err != nil {
			return err
		}
		defer rows.Close()

		products = nil
		for rows.Next() {
			var p pb.Product
			var priceUsd float64
			err := rows.Scan(&p.Id, &p.Name, &p.Description, &p.Picture, &priceUsd, &p.Categories)
			if err != nil {
				return err
			}
			p.PriceUsd = &pb.Money{
				CurrencyCode: "USD",
				Units:        int64(priceUsd),
				Nanos:        int32((priceUsd - float64(int64(priceUsd))) * 1e9),
			}
			products = append(products, &p)
		}
		return rows.Err()
	})
	return products, err
}

// GetProduct returns a single product from the database by ID
func (d *DBCircuitBreaker) GetProduct(ctx context.Context, id string) (*pb.Product, error) {
	var p *pb.Product
	err := d.execute(ctx, "GetProduct", true, func() error {
		var product pb.Product
		var priceUsd float64
		err := d.db.QueryRowContext(ctx, "SELECT id, name, description, picture, price_usd, categories FROM products WHERE id = $1", id).
			Scan(&product.Id, &product.Name, &product.Description, &product.Picture, &priceUsd, &product.Categories)
		if err != nil {
			if errors.Is(err, sql.ErrNoRows) {
				return status.Errorf(codes.NotFound, "product with id %q not found", id)
			}
			return err
		}
		product.PriceUsd = &pb.Money{
			CurrencyCode: "USD",
			Units:        int64(priceUsd),
			Nanos:        int32((priceUsd - float64(int64(priceUsd))) * 1e9),
		}
		p = &product
		return nil
	})
	return p, err
}

// SearchProducts returns products matching the given query string
func (d *DBCircuitBreaker) SearchProducts(ctx context.Context, query string) ([]*pb.Product, error) {
	var products []*pb.Product
	err := d.execute(ctx, "SearchProducts", true, func() error {
		rows, err := d.db.QueryContext(ctx, `
			SELECT id, name, description, picture, price_usd, categories 
			FROM products 
			WHERE name ILIKE '%' || $1 || '%' OR description ILIKE '%' || $1 || '%'
		`, query)
		if err != nil {
			return err
		}
		defer rows.Close()

		products = nil
		for rows.Next() {
			var p pb.Product
			var priceUsd float64
			err := rows.Scan(&p.Id, &p.Name, &p.Description, &p.Picture, &priceUsd, &p.Categories)
			if err != nil {
				return err
			}
			p.PriceUsd = &pb.Money{
				CurrencyCode: "USD",
				Units:        int64(priceUsd),
				Nanos:        int32((priceUsd - float64(int64(priceUsd))) * 1e9),
			}
			products = append(products, &p)
		}
		return rows.Err()
	})
	return products, err
}

func (m *postgresRetryMiddleware) Execute(ctx context.Context, operationName string, isIdempotent bool, op func() error) error {
	if !isIdempotent {
		// Don't retry non-idempotent operations
		return op()
	}

	span := trace.SpanFromContext(ctx)
	span.SetAttributes(
		attribute.Int("db.retry.max_attempts", m.config.MaxAttempts),
	)

	attempt := 0
	var lastErr error

	// Create exponential backoff with jitter
	b := backoff.NewExponentialBackOff()
	b.InitialInterval = m.config.InitialBackoff
	b.MaxInterval = m.config.MaxBackoff
	b.RandomizationFactor = m.config.JitterFactor
	b.Multiplier = 2
	b.Reset()

	for attempt < m.config.MaxAttempts {
		span.SetAttributes(attribute.Int("db.retry.attempt", attempt))

		err := op()
		if err == nil {
			// Success
			if attempt > 0 {
				logger.DebugContext(ctx, "Operation succeeded after retry",
					slog.String("operation", operationName),
					slog.Int("attempts", attempt+1),
				)
			}
			return nil
		}

		lastErr = err
		if !isTransientPostgresError(err) {
			// Non-transient error, don't retry
			span.SetAttributes(attribute.String("db.retry.error_type", "non-transient"))
			return err
		}

		attempt++
		if attempt >= m.config.MaxAttempts {
			break
		}

		// Calculate delay
		delay := b.NextBackOff()
		span.SetAttributes(
			attribute.String("db.retry.error_type", fmt.Sprintf("%T", err)),
			attribute.Int("db.retry.delay_ms", int(delay.Milliseconds())),
		)

		logger.DebugContext(ctx, "Retrying transient PostgreSQL error",
			slog.String("operation", operationName),
			slog.Int("attempt", attempt),
			slog.Duration("delay", delay),
			slog.String("error", err.Error()),
		)

		// Wait for delay or context cancellation
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(delay):
		}
	}

	// All attempts exhausted
	logger.ErrorContext(ctx, "Operation failed after all retry attempts",
		slog.String("operation", operationName),
		slog.Int("total_attempts", m.config.MaxAttempts),
		slog.String("error", lastErr.Error()),
	)
	return lastErr
}

// TLSConfig holds validated TLS configuration for the gRPC server
type TLSConfig struct {
	Enabled              bool
	CertPath             string
	KeyPath              string
	CAPath               string
	ClientAuthRequired   bool
}

// LoadTLSConfigFromEnv reads and validates TLS configuration from environment variables
// Returns error if configuration is invalid
func LoadTLSConfigFromEnv() (TLSConfig, error) {
	var cfg TLSConfig
	
	enabledStr := os.Getenv("PRODUCT_CATALOG_TLS_ENABLED")
	cfg.Enabled = strings.ToLower(enabledStr) == "true"
	
	cfg.CertPath = os.Getenv("PRODUCT_CATALOG_TLS_CERT_PATH")
	cfg.KeyPath = os.Getenv("PRODUCT_CATALOG_TLS_KEY_PATH")
	cfg.CAPath = os.Getenv("PRODUCT_CATALOG_MTLS_CA_CERT_PATH")
	
	clientAuthStr := os.Getenv("PRODUCT_CATALOG_MTLS_ENABLED")
	cfg.ClientAuthRequired = strings.ToLower(clientAuthStr) == "true"
	
	// Validate config
	if cfg.Enabled {
		if cfg.CertPath == "" || cfg.KeyPath == "" {
			return cfg, errors.New("TLS enabled but certificate or private key path not provided")
		}
	}
	
	if cfg.ClientAuthRequired {
		if !cfg.Enabled {
			return cfg, errors.New("mTLS enabled but TLS is not enabled")
		}
		if cfg.CAPath == "" {
			return cfg, errors.New("mTLS enabled but CA certificate path not provided")
		}
	}
	
	return cfg, nil
}

// LoadRateLimitConfigFromEnv reads rate limit configuration from environment variables
func LoadRateLimitConfigFromEnv() RateLimitConfig {
	var cfg RateLimitConfig
	
	rpsStr := os.Getenv("PRODUCT_CATALOG_RATE_LIMIT_RPS")
	if rpsStr != "" {
		if rps, err := strconv.ParseFloat(rpsStr, 64); err == nil {
			cfg.RPS = rps
		}
	}
	
	cfg.IdentifierType = os.Getenv("PRODUCT_CATALOG_RATE_LIMIT_IDENTIFIER")
	if cfg.IdentifierType == "" {
		cfg.IdentifierType = "ip"
	}
	
	return cfg
}

// NewGRPCServerWithTLS creates a gRPC server configured with TLS/mTLS as per the provided config
// Returns plaintext gRPC server if TLS is disabled
// Returns error if TLS configuration is invalid or cannot be loaded
func NewGRPCServerWithTLS(tlsCfg TLSConfig, rateLimitCfg RateLimitConfig) (*grpc.Server, error) {
	// Base server options with OTel handler
	opts := []grpc.ServerOption{
		grpc.StatsHandler(otelgrpc.NewServerHandler()),
	}

	// Add rate limit interceptor if enabled
	if rateLimitCfg.RPS > 0 {
		opts = append(opts, grpc.UnaryInterceptor(RateLimitInterceptor(rateLimitCfg)))
	}

	if !tlsCfg.Enabled {
		// Return plaintext server
		return grpc.NewServer(opts...), nil
	}

	// Load server cert and key
	cert, err := tls.LoadX509KeyPair(tlsCfg.CertPath, tlsCfg.KeyPath)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrTLSInvalidCert, err)
	}

	tlsConfig := &tls.Config{
		Certificates: []tls.Certificate{cert},
		MinVersion:   tls.VersionTLS12,
	}

	if tlsCfg.ClientAuthRequired {
		// Load CA certs for client authentication
		caCert, err := os.ReadFile(tlsCfg.CAPath)
		if err != nil {
			return nil, fmt.Errorf("%w: %v", ErrTLSInvalidCA, err)
		}

		certPool := x509.NewCertPool()
		if !certPool.AppendCertsFromPEM(caCert) {
			return nil, ErrTLSInvalidCA
		}

		tlsConfig.ClientCAs = certPool
		tlsConfig.ClientAuth = tls.RequireAndVerifyClientCert
	}

	// Add TLS credentials to server options
	opts = append(opts, grpc.Creds(credentials.NewTLS(tlsConfig)))

	return grpc.NewServer(opts...), nil
}

// runServer starts the gRPC server on the specified port.
// Returns when the context is cancelled or an error occurs during startup.
func runServer(ctx context.Context, port int) error {
	// Load TLS configuration
	tlsCfg, err := LoadTLSConfigFromEnv()
	if err != nil {
		return fmt.Errorf("invalid TLS configuration: %w", err)
	}

	// Load rate limit configuration
	rateLimitCfg := LoadRateLimitConfigFromEnv()

	ln, err := net.Listen("tcp", fmt.Sprintf(":%d", port))
	if err != nil {
		return fmt.Errorf("TCP listen failed: %w", err)
	}

	srv, err := NewGRPCServerWithTLS(tlsCfg, rateLimitCfg)
	if err != nil {
		return fmt.Errorf("failed to create gRPC server: %w", err)
	}

	svc := &productCatalog{
		retryMiddleware: NewPostgresRetryMiddleware(nil),
	}
	reflection.Register(srv)
	pb.RegisterProductCatalogServiceServer(srv, svc)
	healthpb.RegisterHealthServer(srv, svc)

	// Create a custom handler to route gRPC and HTTP requests
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.ProtoMajor == 2 && strings.HasPrefix(r.Header.Get("Content-Type"), "application/grpc") {
			srv.ServeHTTP(w, r)
		} else {
			http.DefaultServeMux.ServeHTTP(w, r)
		}
	})

	httpSrv := &http.Server{
		Handler: handler,
	}

	errChan := make(chan error, 1)
	go func() {
		if err := httpSrv.Serve(ln); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errChan <- fmt.Errorf("server serve failed: %w", err)
		}
		close(errChan)
	}()

	select {
	case <-ctx.Done():
		// Gracefully stop gRPC server first
		srv.GracefulStop()
		// Then shutdown HTTP server
		shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer shutdownCancel()
		if err := httpSrv.Shutdown(shutdownCtx); err != nil {
			return fmt.Errorf("HTTP server shutdown failed: %w", err)
		}
		return nil
	case err := <-errChan:
		return err
	}
}

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

type productCatalog struct {
	pb.UnimplementedProductCatalogServiceServer
	retryMiddleware PostgresRetryMiddleware
	dbCircuitBreaker *DBCircuitBreaker
}

func init() {
	logger = otelslog.NewLogger("product-catalog")
	// Register health endpoints for tests and runtime
	http.HandleFunc("/health/liveness", healthLivenessHandler)
	http.HandleFunc("/health/readiness", healthReadinessHandler)
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
	catalogLoaded.Store(true)
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
	err = runtime.Start(runtime.WithMinimumReadMemStatsInterval(time.Second))
	if err != nil {
		logger.Error(err.Error())
	}

	// Initialize retry middleware
	retryMiddleware := NewPostgresRetryMiddleware(nil)

	// Initialize circuit breaker
	meter := otel.Meter("product-catalog")
	dbCircuitBreaker, err := NewDBCircuitBreaker(db, meter)
	if err != nil {
		logger.Error(fmt.Sprintf("Failed to initialize database circuit breaker: %v", err))
		os.Exit(1)
	}
	svc := &productCatalog{
		retryMiddleware: retryMiddleware,
		dbCircuitBreaker: dbCircuitBreaker,
	}
	var port string
	mustMapEnv(&port, "PRODUCT_CATALOG_PORT")
	
	// Load TLS configuration
	tlsCfg, err := LoadTLSConfigFromEnv()
	if err != nil {
		logger.Error(fmt.Sprintf("Invalid TLS configuration: %v", err))
		os.Exit(1)
	}

	// Load rate limit configuration
	rateLimitCfg := LoadRateLimitConfigFromEnv()

	logger.Info(fmt.Sprintf("Product Catalog gRPC server started on port: %s", port))

	ln, err := net.Listen("tcp", fmt.Sprintf(":%s", port))
	if err != nil {
		logger.Error(fmt.Sprintf("TCP Listen: %v", err))
		os.Exit(1)
	}

	srv, err := NewGRPCServerWithTLS(tlsCfg, rateLimitCfg)
	if err != nil {
		logger.Error(fmt.Sprintf("Failed to create gRPC server with TLS config: %v", err))
		os.Exit(1)
	}

	reflection.Register(srv)

	pb.RegisterProductCatalogServiceServer(srv, svc)
	// Use our custom health check implementation that verifies DB connectivity
	healthpb.RegisterHealthServer(srv, svc)

	// Create a custom handler to route gRPC and HTTP requests
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.ProtoMajor == 2 && strings.HasPrefix(r.Header.Get("Content-Type"), "application/grpc") {
			srv.ServeHTTP(w, r)
		} else {
			http.DefaultServeMux.ServeHTTP(w, r)
		}
	})

	httpSrv := &http.Server{
		Handler: handler,
	}

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM, syscall.SIGKILL)
	defer cancel()

	go func() {
		if err := httpSrv.Serve(ln); err != nil && !errors.Is(err, http.ErrServerClosed) {
			logger.Error(fmt.Sprintf("Failed to serve server, err: %v", err))
		}
	}()

	<-ctx.Done()

	shutdownInProgress.Store(true)

	// Gracefully stop gRPC server first
	srv.GracefulStop()
	// Then shutdown HTTP server
	shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer shutdownCancel()
	if err := httpSrv.Shutdown(shutdownCtx); err != nil {
		logger.Error(fmt.Sprintf("HTTP server shutdown failed: %v", err))
	}
	logger.Info("Product Catalog server stopped")
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

func healthLivenessHandler(w http.ResponseWriter, r *http.Request) {
	startTime := time.Now()
	w.Header().Set("Content-Type", "application/json")
	var statusCode int
	var response map[string]interface{}
	var errMsg string

	if shutdownInProgress.Load() {
		statusCode = http.StatusServiceUnavailable
		errMsg = "service shutting down"
		response = map[string]interface{}{
			"status": "DOWN",
			"error": errMsg,
		}
	} else {
		statusCode = http.StatusOK
		response = map[string]interface{}{
			"status": "UP",
		}
	}

	w.WriteHeader(statusCode)
	json.NewEncoder(w).Encode(response)

	durationMs := float64(time.Since(startTime).Microseconds()) / 1000.0
	logAttrs := []any{
		slog.String("endpoint", r.URL.Path),
		slog.Int("status_code", statusCode),
		slog.Float64("duration_ms", durationMs),
	}
	if statusCode != http.StatusOK {
		logAttrs = append(logAttrs, slog.String("error", errMsg))
	}
	logger.InfoContext(r.Context(), "Health check request processed", logAttrs...)
}

func healthReadinessHandler(w http.ResponseWriter, r *http.Request) {
	startTime := time.Now()
	w.Header().Set("Content-Type", "application/json")
	var statusCode int
	var response map[string]interface{}
	var errMsg string

	if !catalogLoaded.Load() {
		statusCode = http.StatusServiceUnavailable
		errMsg = "catalog not loaded"
		response = map[string]interface{}{
			"status": "DOWN",
			"error": errMsg,
		}
	} else {
		// Test if DB is accessible with lightweight ping
		pingCtx, cancel := context.WithTimeout(r.Context(), 50*time.Millisecond)
		defer cancel()
		err := db.PingContext(pingCtx)
		if err != nil {
			statusCode = http.StatusServiceUnavailable
			errMsg = fmt.Sprintf("failed to access database: %v", err)
			response = map[string]interface{}{
				"status": "DOWN",
				"error": errMsg,
			}
		} else {
			statusCode = http.StatusOK
			response = map[string]interface{}{
				"status": "UP",
			}
		}
	}

	w.WriteHeader(statusCode)
	json.NewEncoder(w).Encode(response)

	durationMs := float64(time.Since(startTime).Microseconds()) / 1000.0
	logAttrs := []any{
		slog.String("endpoint", r.URL.Path),
		slog.Int("status_code", statusCode),
		slog.Float64("duration_ms", durationMs),
	}
	if statusCode != http.StatusOK {
		logAttrs = append(logAttrs, slog.String("error", errMsg))
	}
	logger.InfoContext(r.Context(), "Health check request processed", logAttrs...)
}

func (p *productCatalog) Check(ctx context.Context, req *healthpb.HealthCheckRequest) (*healthpb.HealthCheckResponse, error) {
	// Add timeout to ensure health check completes within 100ms per AC-3
	pingCtx, cancel := context.WithTimeout(ctx, 50*time.Millisecond)
	defer cancel()

	if err := db.PingContext(pingCtx); err != nil {
		logger.ErrorContext(ctx, "gRPC health check failed: database ping error", slog.Any("error", err))
		return &healthpb.HealthCheckResponse{
			Status: healthpb.HealthCheckResponse_NOT_SERVING,
		}, status.Errorf(codes.Unavailable, "database connection failed: %v", err)
	}

	return &healthpb.HealthCheckResponse{Status: healthpb.HealthCheckResponse_SERVING}, nil
}

func (p *productCatalog) Watch(req *healthpb.HealthCheckRequest, ws healthpb.Health_WatchServer) error {
	return status.Errorf(codes.Unimplemented, "health check via Watch not implemented")
}

func (p *productCatalog) List(ctx context.Context, req *healthpb.HealthListRequest) (*healthpb.HealthListResponse, error) {
	status := healthpb.HealthCheckResponse_SERVING
	if err := db.PingContext(ctx); err != nil {
		status = healthpb.HealthCheckResponse_NOT_SERVING
	}
	return &healthpb.HealthListResponse{
		Statuses: map[string]*healthpb.HealthCheckResponse{
			"": {Status: status},
		},
	}, nil
}

func (p *productCatalog) ListProducts(ctx context.Context, req *pb.ListProductsRequest) (*pb.ListProductsResponse, error) {
	if err := validateListProductsRequest(req); err != nil {
		span := trace.SpanFromContext(ctx)
		span.SetStatus(otelcodes.Error, err.Error())
		return nil, err
	}
	span := trace.SpanFromContext(ctx)

	products, err := p.dbCircuitBreaker.ListProducts(ctx)
	if err != nil {
		span.SetStatus(otelcodes.Error, err.Error())
		// If error is already a gRPC status error, return it directly
		if _, ok := status.FromError(err); ok {
			return nil, err
		}
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

	found, err := p.dbCircuitBreaker.GetProduct(ctx, req.Id)
	if err != nil {
		// If error is already a gRPC status error, return it directly
		if st, ok := status.FromError(err); ok {
			span.SetStatus(otelcodes.Error, st.Message())
			return nil, err
		}
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

	result, err := p.dbCircuitBreaker.SearchProducts(ctx, req.Query)
	if err != nil {
		span.SetStatus(otelcodes.Error, err.Error())
		// If error is already a gRPC status error, return it directly
		if _, ok := status.FromError(err); ok {
			return nil, err
		}
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
