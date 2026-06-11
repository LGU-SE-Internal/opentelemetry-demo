package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"golang.org/x/time/rate"
	"go.opentelemetry.io/contrib/instrumentation/google.golang.org/grpc/otelgrpc"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/log"
	"go.opentelemetry.io/otel/log/global"
	"go.opentelemetry.io/otel/metric"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/health"
	"google.golang.org/grpc/health/grpc_health_v1"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/peer"
	"google.golang.org/grpc/reflection"
	"google.golang.org/grpc/status"

	_ "github.com/lib/pq"
	pb "github.com/open-telemetry/opentelemetry-demo/pb/oteldemo"
)

var isShuttingDown atomic.Bool
var otelLogger = global.GetLoggerProvider().Logger("adservice")
var rateLimitedRequestsCounter metric.Int64Counter

// clientLimiter holds a rate limiter for each client IP and tracks last seen time for cleanup
type clientLimiter struct {
	limiter    *rate.Limiter
	lastSeen   time.Time
}

var (
	limiters = make(map[string]*clientLimiter)
	mu       sync.Mutex
	cleanupInterval = 1 * time.Minute
	limiterTimeout  = 10 * time.Minute
	globalLimiter *rate.Limiter
)

// extractClientId extracts client ID from request metadata or falls back to remote address
func extractClientId(ctx context.Context, remoteAddr string) string {
	// Check for x-client-id header first
	if md, ok := metadata.FromIncomingContext(ctx); ok {
		if clientIDs := md.Get("x-client-id"); len(clientIDs) > 0 && clientIDs[0] != "" {
			return clientIDs[0]
		}
	}

	// Fall back to client IP
	return getClientIP(ctx, remoteAddr)
}

// getClientIP extracts client IP from X-Forwarded-For header or peer address
func getClientIP(ctx context.Context, peerAddr string) string {
	if md, ok := metadata.FromIncomingContext(ctx); ok {
		if xff := md.Get("X-Forwarded-For"); len(xff) > 0 && xff[0] != "" {
			ips := strings.Split(xff[0], ",")
			if len(ips) > 0 {
				return strings.TrimSpace(ips[0])
			}
		}
	}
	// Fall back to peer address, remove port
	if host, _, err := net.SplitHostPort(peerAddr); err == nil {
		return host
	}
	return peerAddr
}

// getLimiter returns the rate limiter for the given client IP, creating one if needed
func getLimiter(clientIP string, rps rate.Limit, burst int) *rate.Limiter {
	mu.Lock()
	defer mu.Unlock()

	if limiter, exists := limiters[clientIP]; exists {
		limiter.lastSeen = time.Now()
		return limiter.limiter
	}

	limiter := rate.NewLimiter(rps, burst)
	limiters[clientIP] = &clientLimiter{
		limiter:  limiter,
		lastSeen: time.Now(),
	}
	return limiter
}

// startLimiterCleanup periodically removes old limiters that haven't been used recently
func startLimiterCleanup() {
	ticker := time.NewTicker(cleanupInterval)
	go func() {
		for range ticker.C {
			mu.Lock()
			for ip, limiter := range limiters {
				if time.Since(limiter.lastSeen) > limiterTimeout {
					delete(limiters, ip)
				}
			}
			mu.Unlock()
		}
	}()
}

// unaryRateLimitInterceptor applies per-client and global rate limiting to unary gRPC requests
func unaryRateLimitInterceptor(perClientRPS rate.Limit, globalRPS rate.Limit, burst int) grpc.UnaryServerInterceptor {
	return func(ctx context.Context, req interface{}, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (interface{}, error) {
		peer, ok := peer.FromContext(ctx)
		clientID := "unknown"
		if ok {
			clientID = extractClientId(ctx, peer.Addr.String())
		}

		// Check per-client rate limit only if enabled
		if perClientRPS > 0 {
			limiter := getLimiter(clientID, perClientRPS, burst)
			if !limiter.Allow() {
				// Increment rate limited metric
				if rateLimitedRequestsCounter != nil {
					rateLimitedRequestsCounter.Add(ctx, 1, metric.WithAttributes(attribute.String("client_id", clientID), attribute.String("limit_type", "per_client")))
				}
				return nil, status.Error(codes.ResourceExhausted, "Per-client rate limit exceeded. Try again later.")
			}
		}

		// Check global rate limit
		if !globalLimiter.Allow() {
			// Increment rate limited metric
			if rateLimitedRequestsCounter != nil {
				rateLimitedRequestsCounter.Add(ctx, 1, metric.WithAttributes(attribute.String("client_id", clientID), attribute.String("limit_type", "global")))
			}
			return nil, status.Error(codes.ResourceExhausted, "Global rate limit exceeded. Try again later.")
		}

		return handler(ctx, req)
	}
}

// streamRateLimitInterceptor applies per-client and global rate limiting to streaming gRPC requests
func streamRateLimitInterceptor(perClientRPS rate.Limit, globalRPS rate.Limit, burst int) grpc.StreamServerInterceptor {
	return func(srv interface{}, ss grpc.ServerStream, info *grpc.StreamServerInfo, handler grpc.StreamHandler) error {
		ctx := ss.Context()
		peer, ok := peer.FromContext(ctx)
		clientID := "unknown"
		if ok {
			clientID = extractClientId(ctx, peer.Addr.String())
		}

		// Check per-client rate limit only if enabled
		if perClientRPS > 0 {
			limiter := getLimiter(clientID, perClientRPS, burst)
			if !limiter.Allow() {
				// Increment rate limited metric
				if rateLimitedRequestsCounter != nil {
					rateLimitedRequestsCounter.Add(ctx, 1, metric.WithAttributes(attribute.String("client_id", clientID), attribute.String("limit_type", "per_client")))
				}
				return status.Error(codes.ResourceExhausted, "Per-client rate limit exceeded. Try again later.")
			}
		}

		// Check global rate limit
		if !globalLimiter.Allow() {
			// Increment rate limited metric
			if rateLimitedRequestsCounter != nil {
				rateLimitedRequestsCounter.Add(ctx, 1, metric.WithAttributes(attribute.String("client_id", clientID), attribute.String("limit_type", "global")))
			}
			return status.Error(codes.ResourceExhausted, "Global rate limit exceeded. Try again later.")
		}

		return handler(srv, ss)
	}
}

// Config holds all service configuration values
type Config struct {
	ServicePort int
	HealthPort  int
	DBHost      string
	DBPort      int
	DBUser      string
	DBPassword  string
	DBName      string
	// New TLS fields
	DBSSLMode     string
	DBSSLRootCert string
	DBSSLCert     string
	DBSSLKey      string
	// Rate limit configuration
	PerClientRateLimit float64
	GlobalRateLimit    float64
	RateLimitBurst     int
}

var allowedSSLMode = map[string]bool{
	"disable":     true,
	"allow":       true,
	"prefer":      true,
	"require":     true,
	"verify-ca":   true,
	"verify-full": true,
}

// LoadConfig reads configuration from environment variables and returns a validated Config instance
// Returns error if:
// - Any integer port value is <1 or >65535
func LoadConfig() (Config, error) {
	cfg := Config{
		ServicePort:          9555,
		HealthPort:           8080,
		DBHost:               "localhost",
		DBPort:               5432,
		DBUser:               "postgres",
		DBPassword:           "postgres",
		DBName:               "ads",
		DBSSLMode:            "disable",
		PerClientRateLimit:   100.0,
		GlobalRateLimit:      10.0,
		RateLimitBurst:       20,
	}

	// Read service port from environment
	if portStr := os.Getenv("AD_SERVICE_PORT"); portStr != "" {
		port, err := strconv.Atoi(portStr)
		if err != nil {
			return cfg, fmt.Errorf("invalid AD_SERVICE_PORT: %w", err)
		}
		if port < 1 || port > 65535 {
			return cfg, fmt.Errorf("AD_SERVICE_PORT must be between 1 and 65535, got %d", port)
		}
		cfg.ServicePort = port
	}

	// Read health port from environment
	if portStr := os.Getenv("AD_SERVICE_HEALTH_PORT"); portStr != "" {
		port, err := strconv.Atoi(portStr)
		if err != nil {
			return cfg, fmt.Errorf("invalid AD_SERVICE_HEALTH_PORT: %w", err)
		}
		if port < 1 || port > 65535 {
			return cfg, fmt.Errorf("AD_SERVICE_HEALTH_PORT must be between 1 and 65535, got %d", port)
		}
		cfg.HealthPort = port
	}

	// Read per client rate limit from environment
	if rpsStr := os.Getenv("AD_SERVICE_PER_CLIENT_RATE_LIMIT"); rpsStr != "" {
		rps, err := strconv.ParseFloat(rpsStr, 64)
		if err != nil {
			return cfg, fmt.Errorf("invalid AD_SERVICE_PER_CLIENT_RATE_LIMIT: %w", err)
		}
		cfg.PerClientRateLimit = rps
	}

	// Read global rate limit from environment
	if rpsStr := os.Getenv("AD_SERVICE_GLOBAL_RATE_LIMIT"); rpsStr != "" {
		rps, err := strconv.ParseFloat(rpsStr, 64)
		if err != nil {
			return cfg, fmt.Errorf("invalid AD_SERVICE_GLOBAL_RATE_LIMIT: %w", err)
		}
		if rps <= 0 {
			return cfg, fmt.Errorf("AD_SERVICE_GLOBAL_RATE_LIMIT must be positive, got %f", rps)
		}
		cfg.GlobalRateLimit = rps
	}

	// Read rate limit burst from environment
	if burstStr := os.Getenv("AD_SERVICE_RATE_LIMIT_BURST"); burstStr != "" {
		burst, err := strconv.Atoi(burstStr)
		if err != nil {
			return cfg, fmt.Errorf("invalid AD_SERVICE_RATE_LIMIT_BURST: %w", err)
		}
		if burst <= 0 {
			return cfg, fmt.Errorf("AD_SERVICE_RATE_LIMIT_BURST must be positive, got %d", burst)
		}
		cfg.RateLimitBurst = burst
	}

	// Read database host from environment
	if host := os.Getenv("AD_DB_HOST"); host != "" {
		cfg.DBHost = host
	}

	// Read database port from environment
	if portStr := os.Getenv("AD_DB_PORT"); portStr != "" {
		port, err := strconv.Atoi(portStr)
		if err != nil {
			return cfg, fmt.Errorf("invalid AD_DB_PORT: %w", err)
		}
		if port < 1 || port > 65535 {
			return cfg, fmt.Errorf("AD_DB_PORT must be between 1 and 65535, got %d", port)
		}
		cfg.DBPort = port
	}

	// Read database user from environment
	if user := os.Getenv("AD_DB_USER"); user != "" {
		cfg.DBUser = user
	}

	// Read database password from environment
	if pass := os.Getenv("AD_DB_PASSWORD"); pass != "" {
		cfg.DBPassword = pass
	}

	// Read database name from environment
	if name := os.Getenv("AD_DB_NAME"); name != "" {
		cfg.DBName = name
	}

	// Read TLS configuration from environment
	if sslMode := os.Getenv("POSTGRES_SSLMODE"); sslMode != "" {
		cfg.DBSSLMode = sslMode
	}
	if rootCert := os.Getenv("POSTGRES_SSLROOTCERT"); rootCert != "" {
		cfg.DBSSLRootCert = rootCert
	}
	if sslCert := os.Getenv("POSTGRES_SSLCERT"); sslCert != "" {
		cfg.DBSSLCert = sslCert
	}
	if sslKey := os.Getenv("POSTGRES_SSLKEY"); sslKey != "" {
		cfg.DBSSLKey = sslKey
	}

	// Validate SSL mode
	if !allowedSSLMode[cfg.DBSSLMode] {
		return cfg, fmt.Errorf("invalid POSTGRES_SSLMODE value: %q, allowed values are: disable, allow, prefer, require, verify-ca, verify-full", cfg.DBSSLMode)
	}

	// Validate root cert is provided for verify-ca and verify-full modes
	if (cfg.DBSSLMode == "verify-ca" || cfg.DBSSLMode == "verify-full") && cfg.DBSSLRootCert == "" {
		return cfg, fmt.Errorf("POSTGRES_SSLROOTCERT is required when POSTGRES_SSLMODE is %q", cfg.DBSSLMode)
	}

	// Validate client cert and key are both provided if either is present
	if cfg.DBSSLCert != "" && cfg.DBSSLKey == "" {
		return cfg, fmt.Errorf("POSTGRES_SSLKEY is required when POSTGRES_SSLCERT is provided")
	}
	if cfg.DBSSLKey != "" && cfg.DBSSLCert == "" {
		return cfg, fmt.Errorf("POSTGRES_SSLCERT is required when POSTGRES_SSLKEY is provided")
	}

	// Validate certificate files are readable
	if cfg.DBSSLRootCert != "" {
		if _, err := os.Stat(cfg.DBSSLRootCert); err != nil {
			return cfg, fmt.Errorf("cannot read POSTGRES_SSLROOTCERT file %q: %w", cfg.DBSSLRootCert, err)
		}
	}
	if cfg.DBSSLCert != "" {
		if _, err := os.Stat(cfg.DBSSLCert); err != nil {
			return cfg, fmt.Errorf("cannot read POSTGRES_SSLCERT file %q: %w", cfg.DBSSLCert, err)
		}
	}
	if cfg.DBSSLKey != "" {
		if _, err := os.Stat(cfg.DBSSLKey); err != nil {
			return cfg, fmt.Errorf("cannot read POSTGRES_SSLKEY file %q: %w", cfg.DBSSLKey, err)
		}
	}

	return cfg, nil
}

const (
	shutdownWindow = 10 * time.Second
)

func healthHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	if isShuttingDown.Load() {
		w.WriteHeader(http.StatusServiceUnavailable)
		json.NewEncoder(w).Encode(map[string]string{"status": "unavailable"})
		return
	}
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}

func readinessHandler(db *sql.DB) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if err := db.PingContext(r.Context()); err != nil {
			w.WriteHeader(http.StatusServiceUnavailable)
			json.NewEncoder(w).Encode(map[string]string{
				"status":   "not_ready",
				"database": "disconnected",
			})
			return
		}
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(map[string]string{
			"status":   "ready",
			"database": "connected",
		})
	}
}

	// Load configuration
	cfg, err := LoadConfig()
	if err != nil {
		otelLogger.Emit(context.Background(), log.Record{
			Severity: log.SeverityError,
			Body:     log.StringValue(fmt.Sprintf("Failed to load configuration: %v", err)),
		})
		os.Exit(1)
	}

	// Initialize rate limit metric
	meter := global.MeterProvider().Meter("adservice")
	rateLimitedRequestsCounter, err = meter.Int64Counter(
		"adservice_rate_limited_requests_total",
		metric.WithDescription("Total number of requests that were rejected due to rate limiting"),
	)
	if err != nil {
		otelLogger.Emit(context.Background(), log.Record{
			Severity: log.SeverityError,
			Body:     log.StringValue(fmt.Sprintf("Failed to create rate limit metric: %v", err)),
		})
		os.Exit(1)
	}

	// Initialize global rate limiter
	globalLimiter = rate.NewLimiter(rate.Limit(cfg.GlobalRateLimit), cfg.RateLimitBurst)

	// Start limiter cleanup goroutine
	startLimiterCleanup()

	// Create gRPC health server first so we can set status during DB initialization
	healthServer := health.NewServer()

	// Initialize database connection
	portStr := strconv.Itoa(cfg.DBPort)
	connStr := fmt.Sprintf("host=%s port=%s user=%s password=%s dbname=%s sslmode=%s",
		cfg.DBHost, portStr, cfg.DBUser, cfg.DBPassword, cfg.DBName, cfg.DBSSLMode)
	if cfg.DBSSLRootCert != "" {
		connStr += fmt.Sprintf(" sslrootcert=%s", cfg.DBSSLRootCert)
	}
	if cfg.DBSSLCert != "" {
		connStr += fmt.Sprintf(" sslcert=%s", cfg.DBSSLCert)
	}
	if cfg.DBSSLKey != "" {
		connStr += fmt.Sprintf(" sslkey=%s", cfg.DBSSLKey)
	}
		dbConn, err := sql.Open("postgres", connStr)
		if err != nil {
			otelLogger.Emit(context.Background(), log.Record{
				Severity: log.SeverityError,
				Body:     log.StringValue(fmt.Sprintf("Failed to initialize database connection: %v", err)),
			})
			os.Exit(1)
		}
	defer dbConn.Close()

        // Attempt initial DB ping but don't fail on error - let readiness check handle it
        if err := dbConn.Ping(); err != nil {
                otelLogger.Emit(context.Background(), log.Record{
                        Severity: log.SeverityWarn,
                        Body:     log.StringValue(fmt.Sprintf("Warning: Initial database ping failed: %v", err)),
                })
                healthServer.SetServingStatus("", grpc_health_v1.HealthCheckResponse_NOT_SERVING)
        } else {
                otelLogger.Emit(context.Background(), log.Record{
                        Severity: log.SeverityInfo,
                        Body:     log.StringValue("Successfully connected to database"),
                })
                healthServer.SetServingStatus("", grpc_health_v1.HealthCheckResponse_SERVING)
        }

	// Create gRPC server
	listenAddr := fmt.Sprintf(":%d", cfg.ServicePort)
	lis, err := net.Listen("tcp", listenAddr)
	if err != nil {
		otelLogger.Emit(context.Background(), log.Record{
			Severity: log.SeverityError,
			Body:     log.StringValue(fmt.Sprintf("Failed to listen on %s: %v", listenAddr, err)),
		})
		os.Exit(1)
	}

	// Create rate limit interceptors
	perClientRate := rate.Limit(cfg.PerClientRateLimit)
	globalRate := rate.Limit(cfg.GlobalRateLimit)
	burst := cfg.RateLimitBurst
	unaryRateLimiter := unaryRateLimitInterceptor(perClientRate, globalRate, burst)
	streamRateLimiter := streamRateLimitInterceptor(perClientRate, globalRate, burst)

	// Chain interceptors: rate limit runs first, then otel
	s := grpc.NewServer(
		grpc.ChainUnaryInterceptor(
			unaryRateLimiter,
			otelgrpc.UnaryServerInterceptor(),
		),
		grpc.ChainStreamInterceptor(
			streamRateLimiter,
			otelgrpc.StreamServerInterceptor(),
		),
	)
	pb.RegisterAdServiceServer(s, &adService{db: dbConn})
	reflection.Register(s)

	// Register gRPC health check service
	grpc_health_v1.RegisterHealthServer(s, healthServer)

	// Set up HTTP health server
	mux := http.NewServeMux()
	mux.HandleFunc("/health", healthHandler)
	mux.HandleFunc("/readiness", readinessHandler(dbConn))
	httpServer := &http.Server{
		Addr:    fmt.Sprintf(":%d", cfg.HealthPort),
		Handler: mux,
	}

	// Start HTTP health server in goroutine
	go func() {
		otelLogger.Emit(context.Background(), log.Record{
			Severity: log.SeverityInfo,
			Body:     log.StringValue(fmt.Sprintf("Health endpoints starting on :%d", cfg.HealthPort)),
		})
		if err := httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			otelLogger.Emit(context.Background(), log.Record{
				Severity: log.SeverityError,
				Body:     log.StringValue(fmt.Sprintf("Failed to start health server: %v", err)),
			})
			os.Exit(1)
		}
	}()

	// Start background DB health check goroutine
	go func() {
		ticker := time.NewTicker(5 * time.Second)
		defer ticker.Stop()
		for range ticker.C {
			if isShuttingDown.Load() {
				return
			}
			if err := dbConn.Ping(); err != nil {
				healthServer.SetServingStatus("", grpc_health_v1.HealthCheckResponse_NOT_SERVING)
			} else {
				healthServer.SetServingStatus("", grpc_health_v1.HealthCheckResponse_SERVING)
			}
		}
	}()

	// Set up signal handler for graceful shutdown
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)

	go func() {
		<-sigChan
		isShuttingDown.Store(true)
		// Update health status to not serving
		healthServer.SetServingStatus("", grpc_health_v1.HealthCheckResponse_NOT_SERVING)
		otelLogger.Emit(context.Background(), log.Record{
			Severity: log.SeverityInfo,
			Body:     log.StringValue("INFO: Starting graceful shutdown, waiting up to 10s for in-flight requests to complete"),
		})
		// Shutdown HTTP server
		httpCtx, httpCancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer httpCancel()
		if err := httpServer.Shutdown(httpCtx); err != nil {
			otelLogger.Emit(context.Background(), log.Record{
				Severity: log.SeverityWarn,
				Body:     log.StringValue(fmt.Sprintf("WARN: HTTP server shutdown failed: %v", err)),
			})
		}
		err := GracefulShutdown(s, dbConn, shutdownWindow)
		if err != nil {
			if err == context.DeadlineExceeded {
				otelLogger.Emit(context.Background(), log.Record{
					Severity: log.SeverityWarn,
					Body:     log.StringValue("WARN: Graceful shutdown timed out after 10s, force closing remaining connections"),
				})
				os.Exit(1)
			}
			otelLogger.Emit(context.Background(), log.Record{
				Severity: log.SeverityError,
				Body:     log.StringValue(fmt.Sprintf("ERROR: Graceful shutdown failed: %v", err)),
			})
			os.Exit(1)
		}
		otelLogger.Emit(context.Background(), log.Record{
			Severity: log.SeverityInfo,
			Body:     log.StringValue("INFO: Successfully closed database connection"),
		})
		os.Exit(0)
	}()

	otelLogger.Emit(context.Background(), log.Record{
		Severity: log.SeverityInfo,
		Body:     log.StringValue(fmt.Sprintf("Ad service starting on %s", listenAddr)),
	})
	if err := s.Serve(lis); err != nil && err != grpc.ErrServerStopped {
		otelLogger.Emit(context.Background(), log.Record{
			Severity: log.SeverityError,
			Body:     log.StringValue(fmt.Sprintf("Failed to serve: %v", err)),
		})
		os.Exit(1)
	}
}

// adService implements the AdService gRPC interface
type adService struct {
	pb.UnimplementedAdServiceServer
	db *sql.DB
}

// GetAds returns ads based on the request context
func (s *adService) GetAds(ctx context.Context, req *pb.GetAdsRequest) (*pb.GetAdsResponse, error) {
	// Simple implementation - return dummy ads for now
	return &pb.GetAdsResponse{
		Ads: []*pb.Ad{
			{
				Text: "Sample ad",
				Url:  "https://example.com",
			},
		},
	}, nil
}

// GracefulShutdown triggers the shutdown sequence for the ad service
// Parameters:
//   grpcServer *grpc.Server: Running gRPC server instance
//   dbConn *sql.DB: Active database connection instance
//   timeout time.Duration: Maximum wait time for in-flight requests
// Returns:
//   error: Non-nil if shutdown timed out or resource close failed
func GracefulShutdown(grpcServer *grpc.Server, dbConn *sql.DB, timeout time.Duration) error {
	// Create context with timeout for shutdown
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	// Stop accepting new requests and wait for in-flight to complete
	shutdownDone := make(chan struct{})
	go func() {
		grpcServer.GracefulStop()
		close(shutdownDone)
	}()

	// Wait for either shutdown completion or timeout
	select {
	case <-shutdownDone:
		// Shutdown complete, close database connection
		if err := dbConn.Close(); err != nil {
			return err
		}
		return nil
	case <-ctx.Done():
		// Timeout, force stop gRPC server
		grpcServer.Stop()
		// Try to close DB connection anyway
		_ = dbConn.Close()
		return context.DeadlineExceeded
	}
}
