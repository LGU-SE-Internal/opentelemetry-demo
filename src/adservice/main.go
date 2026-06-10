package main

import (
	"context"
	"database/sql"
	"fmt"
	"log"
	"net"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"go.opentelemetry.io/contrib/instrumentation/google.golang.org/grpc/otelgrpc"
	"google.golang.org/grpc"
	"google.golang.org/grpc/reflection"

	_ "github.com/lib/pq"
	pb "github.com/open-telemetry/opentelemetry-demo/pb/oteldemo"
)

// Config holds all service configuration values
type Config struct {
	ServicePort int
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
		ServicePort: 9555,
		DBHost:      "localhost",
		DBPort:      5432,
		DBUser:      "postgres",
		DBPassword:  "postgres",
		DBName:      "ads",
		DBSSLMode:   "disable",
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

var logger = log.New(os.Stdout, "[adservice] ", log.LstdFlags|log.Lshortfile)

func main() {
	// Load configuration
	cfg, err := LoadConfig()
	if err != nil {
		logger.Fatalf("Failed to load configuration: %v", err)
	}

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
		logger.Fatalf("Failed to connect to database: %v", err)
	}
	defer dbConn.Close()

	if err := dbConn.Ping(); err != nil {
		logger.Fatalf("Failed to ping database: %v", err)
	}
	logger.Println("Successfully connected to database")

	// Create gRPC server
	listenAddr := fmt.Sprintf(":%d", cfg.ServicePort)
	lis, err := net.Listen("tcp", listenAddr)
	if err != nil {
		logger.Fatalf("Failed to listen on %s: %v", listenAddr, err)
	}

	s := grpc.NewServer(
		grpc.UnaryInterceptor(otelgrpc.UnaryServerInterceptor()),
		grpc.StreamInterceptor(otelgrpc.StreamServerInterceptor()),
	)
	pb.RegisterAdServiceServer(s, &adService{db: dbConn})
	reflection.Register(s)

	// Set up signal handler for graceful shutdown
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)

	go func() {
		<-sigChan
		logger.Println("INFO: Starting graceful shutdown, waiting up to 10s for in-flight requests to complete")
		err := GracefulShutdown(s, dbConn, shutdownWindow)
		if err != nil {
			if err == context.DeadlineExceeded {
				logger.Println("WARN: Graceful shutdown timed out after 10s, force closing remaining connections")
				os.Exit(1)
			}
			logger.Printf("ERROR: Graceful shutdown failed: %v", err)
			os.Exit(1)
		}
		logger.Println("INFO: Successfully closed database connection")
		os.Exit(0)
	}()

	logger.Printf("Ad service starting on %s", listenAddr)
	if err := s.Serve(lis); err != nil && err != grpc.ErrServerStopped {
		logger.Fatalf("Failed to serve: %v", err)
	}
}

// adService implements the AdService gRPC interface
type adService struct {
	pb.UnimplementedAdServiceServer
	db *sql.DB
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
