package main

import (
	"context"
	"database/sql"
	"log"
	"net"
	"os"
	"os/signal"
	"syscall"
	"time"

	"go.opentelemetry.io/contrib/instrumentation/google.golang.org/grpc/otelgrpc"
	"google.golang.org/grpc"
	"google.golang.org/grpc/reflection"

	_ "github.com/lib/pq"
	pb "github.com/open-telemetry/opentelemetry-demo/pb/oteldemo"
)

const (
	port           = ":9555"
	dbHost         = "postgresql"
	dbPort         = "5432"
	dbUser         = "postgres"
	dbPassword     = "postgres"
	dbName         = "adservice"
	shutdownWindow = 10 * time.Second
)

var logger = log.New(os.Stdout, "[adservice] ", log.LstdFlags|log.Lshortfile)

func main() {
	// Initialize database connection
	dbConn, err := sql.Open("postgres",
		"host="+dbHost+" port="+dbPort+" user="+dbUser+" password="+dbPassword+" dbname="+dbName+" sslmode=disable")
	if err != nil {
		logger.Fatalf("Failed to connect to database: %v", err)
	}
	defer dbConn.Close()

	if err := dbConn.Ping(); err != nil {
		logger.Fatalf("Failed to ping database: %v", err)
	}
	logger.Println("Successfully connected to database")

	// Create gRPC server
	lis, err := net.Listen("tcp", port)
	if err != nil {
		logger.Fatalf("Failed to listen: %v", err)
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

	logger.Printf("Ad service starting on %s", port)
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
