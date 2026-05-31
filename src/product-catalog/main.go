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
	"strings"
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

	otelhooks "github.com/open-feature/go-sdk-contrib/hooks/open-telemetry/pkg"
	flagd "github.com/open-feature/go-sdk-contrib/providers/flagd/pkg"
	"github.com/open-feature/go-sdk/openfeature"
	pb "github.com/opentelemetry/opentelemetry-demo/src/product-catalog/genproto/oteldemo"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/health"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"
	"google.golang.org/grpc/reflection"
	"google.golang.org/grpc/status"

	"github.com/XSAM/otelsql"
	flags "github.com/opentelemetry/opentelemetry-demo/src/product-catalog/flags"
)

type productCatalog struct {
	pb.UnimplementedProductCatalogServiceServer
}

var (
	logger *slog.Logger
	db     *sql.DB
	reg    metric.Registration
	envCount int
)

func mustMapEnv(target *string, key string, expectedType string, allowedValues []string) {
	value := os.Getenv(key)
	if value == "" {
		errorMsg := fmt.Sprintf("Invalid environment variable configuration:\n  Variable name: %s\n  Invalid value: <not set>\n  Expected: Required %s value", key, expectedType)
		if len(allowedValues) > 0 {
			errorMsg += fmt.Sprintf(", allowed values: %s", strings.Join(allowedValues, ", "))
		}
		fmt.Fprintln(os.Stderr, errorMsg)
		os.Exit(1)
	}
	
	// Validate format based on type
	if expectedType == "TCP port number" {
		port, err := strconv.Atoi(value)
		if err != nil || port < 1 || port > 65535 {
			errorMsg := fmt.Sprintf("Invalid environment variable configuration:\n  Variable name: %s\n  Invalid value: %s\n  Expected: %s between 1 and 65535", key, value, expectedType)
			fmt.Fprintln(os.Stderr, errorMsg)
			os.Exit(1)
		}
	}
	
	// Validate against allowed values
	if len(allowedValues) > 0 {
		found := false
		for _, allowed := range allowedValues {
			if value == allowed {
				found = true
				break
			}
		}
		if !found {
			errorMsg := fmt.Sprintf("Invalid environment variable configuration:\n  Variable name: %s\n  Invalid value: %s\n  Expected: %s, allowed values: %s", key, value, expectedType, strings.Join(allowedValues, ", "))
			fmt.Fprintln(os.Stderr, errorMsg)
			os.Exit(1)
		}
	}
	
	*target = value
	envCount++
}

func init() {
	logger = otelslog.NewLogger("product-catalog")
}

func initDatabase() error {
	var connStr string
	mustMapEnv(&connStr, "DB_CONNECTION_STRING", "PostgreSQL connection string", []string{})
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

	svc := &productCatalog{}
		var port string
	mustMapEnv(&port, "PRODUCT_CATALOG_PORT", "TCP port number", []string{})

	logger.Info("All required environment variables validated successfully",
		slog.Int("validated_env_vars", envCount),
		slog.Bool("configuration_valid", true))

	logger.Info(fmt.Sprintf("Product Catalog gRPC server started on port: %s", port))

	ln, err := net.Listen("tcp", fmt.Sprintf(":%s", port))
	if err != nil {
		logger.Error(fmt.Sprintf("TCP Listen: %v", err))
	}

	srv := grpc.NewServer(
		grpc.StatsHandler(otelgrpc.NewServerHandler()),
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

		if err := rows.Scan(&id, &name, &description, &picture, &currencyCode, &units, &n
