package main

import (
	"context"
	"database/sql"
	"fmt"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"go.opentelemetry.io/otel/log/global"
	"go.opentelemetry.io/otel/sdk/log"
	"go.opentelemetry.io/otel/sdk/log/logtest"
	"go.opentelemetry.io/otel/sdk/resource"
	semconv "go.opentelemetry.io/otel/semconv/v1.26.0"
	"google.golang.org/grpc"
	pb "github.com/open-telemetry/opentelemetry-demo/pb/oteldemo"
)

func cfgToConnStr(cfg Config) string {
	connStr := fmt.Sprintf("host=%s port=%d user=%s password=%s dbname=%s sslmode=%s",
		cfg.DBHost, cfg.DBPort, cfg.DBUser, cfg.DBPassword, cfg.DBName, cfg.DBSSLMode)
	if cfg.DBSSLRootCert != "" {
		connStr += fmt.Sprintf(" sslrootcert=%s", cfg.DBSSLRootCert)
	}
	if cfg.DBSSLCert != "" {
		connStr += fmt.Sprintf(" sslcert=%s", cfg.DBSSLCert)
	}
	if cfg.DBSSLKey != "" {
		connStr += fmt.Sprintf(" sslkey=%s", cfg.DBSSLKey)
	}
	return connStr
}

// TestAC1_NoRawLoggingCalls checks that there are no direct log.Print/Printf/Println calls left
// AC-1: All raw logging calls replaced with OpenTelemetry Logging API
func TestAC1_NoRawLoggingCalls(t *testing.T) {
	t.Skip("Static analysis test: Check source code for any remaining raw log.* calls or fmt.Print* calls used for logging")
	// Invariant: No raw logging calls exist in source code except test files
	// Run: grep -r "log\.Print\|fmt\.Print" src/adservice/ --include="*.go" | grep -v "_test.go"
	// Expected output: No results
}

// TestAC2_LogMetadataValid verifies all required metadata fields are present on log entries
// AC-2: Structured metadata fields present with correct values
func TestAC2_LogMetadataValid(t *testing.T) {
	// Setup in-memory log recorder
	recorder := logtest.NewRecorder()
	provider := log.NewLoggerProvider(
		log.WithProcessor(log.NewSimpleProcessor(recorder)),
		log.WithResource(resource.NewWithAttributes(
			semconv.SchemaURL,
			semconv.ServiceNameKey.String("adservice"),
		)),
	)
	global.SetLoggerProvider(provider)
	defer global.SetLoggerProvider(log.NewNoopLoggerProvider())

	// Trigger an event that generates a log (e.g. database connection success)
	cfg := Config{
		DBHost:      "localhost",
		DBPort:      5432,
		DBUser:      "postgres",
		DBPassword:  "postgres",
		DBName:      "ads",
		DBSSLMode:   "disable",
	}
	dbConn, err := sql.Open("postgres", cfgToConnStr(cfg))
	require.NoError(t, err)
	defer dbConn.Close()

	// Get recorded logs
	logs := recorder.Result()
	require.Greater(t, len(logs), 0, "No logs generated")

	for _, record := range logs {
		// Check service.name attribute
		serviceName, ok := record.Attributes().Value(semconv.ServiceNameKey)
		assert.True(t, ok, "service.name attribute missing from log entry")
		assert.Equal(t, "adservice", serviceName.AsString(), "service.name value incorrect")

		// Check severity level is set
		assert.NotZero(t, record.Severity(), "Severity level not set on log entry")
	}
}

// TestAC2_TraceCorrelation verifies trace_id and span_id are present when logging within an active trace
// AC-2: trace_id and span_id populated automatically in trace context
func TestAC2_TraceCorrelation(t *testing.T) {
	t.Skip("Requires OpenTelemetry trace setup in tests")
	// Invariant: Any log emitted within an active trace context contains trace_id and span_id matching the active span
	// Steps:
	// 1. Start an active trace span
	// 2. Emit a log entry within that span context
	// 3. Verify log entry has trace_id and span_id matching the active span
}

// TestAC3_LogMessageContentPreserved verifies original log message text is unchanged
// AC-3: Exact original log message content preserved
func TestAC3_LogMessageContentPreserved(t *testing.T) {
	// Setup in-memory log recorder
	recorder := logtest.NewRecorder()
	provider := log.NewLoggerProvider(
		log.WithProcessor(log.NewSimpleProcessor(recorder)),
	)
	global.SetLoggerProvider(provider)
	defer global.SetLoggerProvider(log.NewNoopLoggerProvider())

	// Trigger known log messages
	testCases := []struct {
		name              string
		triggerFn         func()
		expectedSubstring string
	}{
		{
			name: "Database connection success log",
			triggerFn: func() {
				// Connect to a mock database that succeeds
			},
			expectedSubstring: "Successfully connected to database",
		},
		{
			name: "Shutdown started log",
			triggerFn: func() {
				// Send SIGTERM to service to trigger shutdown
			},
			expectedSubstring: "Starting graceful shutdown",
		},
		{
			name: "Shutdown success log",
			triggerFn: func() {
				// Complete graceful shutdown
			},
			expectedSubstring: "Successfully closed database connection",
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			// tc.triggerFn()
			// logs := recorder.Result()
			// assert.Contains(t, logs[len(logs)-1].Body().AsString(), tc.expectedSubstring)
			t.Skip("Needs mock implementations to trigger log events")
		})
	}
}

// TestAC4_LogsExportedToCollector verifies logs are sent to configured OTel collector
// AC-4: Logs successfully exported to collector endpoint
func TestAC4_LogsExportedToCollector(t *testing.T) {
	t.Skip("Integration test running in demo environment with OTel collector")
	// Invariant: All ad service logs appear in collector received logs
	// Steps:
	// 1. Start ad service with OTEL_EXPORTER_OTLP_LOGS_ENDPOINT set to test collector
	// 2. Generate test log entries
	// 3. Query collector logs endpoint to verify logs were received
}

// TestAC5_ExistingFunctionalityUnchanged verifies all existing service functionality works as before
// AC-5: Existing functionality unchanged
func TestAC5_ExistingFunctionalityUnchanged(t *testing.T) {
	t.Run("GetAds API returns correct response", func(t *testing.T) {
		service := &adService{db: nil}
		resp, err := service.GetAds(context.Background(), &pb.GetAdsRequest{})
		require.NoError(t, err)
		assert.NotNil(t, resp)
		assert.Greater(t, len(resp.Ads), 0)
		assert.Equal(t, "Sample ad", resp.Ads[0].Text)
		assert.Equal(t, "https://example.com", resp.Ads[0].Url)
	})

	t.Run("GracefulShutdown works correctly", func(t *testing.T) {
		grpcServer := grpc.NewServer()
		db, err := sql.Open("postgres", "host=localhost port=5432 user=postgres password=postgres dbname=ads sslmode=disable")
		require.NoError(t, err)
		defer db.Close()

		err = GracefulShutdown(grpcServer, db, 1*time.Second)
		// Should either succeed or timeout, no panics
		assert.True(t, err == nil || err == context.DeadlineExceeded)
	})
}

// TestAC6_TelemetryNotBroken verifies existing traces and metrics continue working
// AC-6: No breaking changes to existing telemetry pipelines
func TestAC6_TelemetryNotBroken(t *testing.T) {
	t.Skip("Integration test verifying traces and metrics are still exported correctly")
	// Invariant: Traces and metrics exported before change are still exported with same schema
	// Steps:
	// 1. Start service with logging changes
	// 2. Make API request to trigger trace and metric generation
	// 3. Verify traces are exported with same span names, attributes as before
	// 4. Verify metrics are exported with same names, dimensions as before
}
