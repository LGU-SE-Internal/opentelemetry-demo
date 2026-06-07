#include <gtest/gtest.h>
#include <grpcpp/grpcpp.h>
#include <chrono>
#include <thread>
#include <vector>
#include "src/proto/demo/currency_service.grpc.pb.h"
#include "opentelemetry/metrics/provider.h"
#include "opentelemetry/metrics/sync_instruments.h"

using opentelemetry::proto::collector::metrics::v1::ExportMetricsServiceRequest;
using grpc::ClientContext;
using grpc::Status;
using demo::CurrencyService;
using demo::ConversionRequest;
using demo::ConversionResponse;

namespace {

const std::string kServiceAddress = "localhost:8081";
constexpr int kDefaultRPM = 100;
constexpr char kRateLimitExceededMessagePrefix[] = "Rate limit exceeded. Maximum ";
constexpr char kRateLimitExceededMessageSuffix[] = " requests per minute per IP address.";

std::unique_ptr<CurrencyService::Stub> CreateStub() {
  auto channel = grpc::CreateChannel(kServiceAddress, grpc::InsecureChannelCredentials());
  return CurrencyService::NewStub(channel);
}

ConversionRequest CreateTestRequest() {
  ConversionRequest req;
  req.set_from_currency("USD");
  req.set_to_currency("EUR");
  req.add_units(1);
  req.add_nanos(0);
  return req;
}

// AC-1: Default rate limit of 100 requests per minute per client IP
TEST(CurrencyRateLimitTest, test_ac1_default_rate_limit_exceeded) {
  auto stub = CreateStub();
  ConversionRequest req = CreateTestRequest();
  std::vector<Status> results;

  // Send 101 requests within 60 seconds
  for (int i = 0; i < kDefaultRPM + 1; ++i) {
    ClientContext ctx;
    ConversionResponse resp;
    Status s = stub->Convert(&ctx, req, &resp);
    results.push_back(s);
  }

  // First 100 should be OK, last 1 should be RESOURCE_EXHAUSTED
  for (int i = 0; i < kDefaultRPM; ++i) {
    EXPECT_TRUE(results[i].ok()) << "Request " << i << " should succeed";
  }
  EXPECT_EQ(results[kDefaultRPM].error_code(), grpc::StatusCode::RESOURCE_EXHAUSTED)
      << "101st request should be rate limited";
}

// AC-2: Custom rate limit via CURRENCY_SERVICE_RATE_LIMIT_RPM env var
TEST(CurrencyRateLimitTest, test_ac2_custom_rate_limit) {
  // Note: This test assumes the service was started with CURRENCY_SERVICE_RATE_LIMIT_RPM=200
  const int kCustomRPM = 200;
  auto stub = CreateStub();
  ConversionRequest req = CreateTestRequest();
  std::vector<Status> results;

  // Send 201 requests
  for (int i = 0; i < kCustomRPM + 1; ++i) {
    ClientContext ctx;
    ConversionResponse resp;
    Status s = stub->Convert(&ctx, req, &resp);
    results.push_back(s);
  }

  for (int i = 0; i < kCustomRPM; ++i) {
    EXPECT_TRUE(results[i].ok()) << "Request " << i << " should succeed with custom limit";
  }
  EXPECT_EQ(results[kCustomRPM].error_code(), grpc::StatusCode::RESOURCE_EXHAUSTED)
      << "201st request should be rate limited with custom limit of 200";
}

// AC-3: Rate limiting disabled when CURRENCY_SERVICE_RATE_LIMIT_ENABLED=false
TEST(CurrencyRateLimitTest, test_ac3_rate_limiting_disabled) {
  // Note: This test assumes the service was started with CURRENCY_SERVICE_RATE_LIMIT_ENABLED=false
  auto stub = CreateStub();
  ConversionRequest req = CreateTestRequest();

  // Send 200 requests (double default limit)
  for (int i = 0; i < 2 * kDefaultRPM; ++i) {
    ClientContext ctx;
    ConversionResponse resp;
    Status s = stub->Convert(&ctx, req, &resp);
    EXPECT_TRUE(s.ok()) << "Request " << i << " should succeed when rate limiting is disabled";
  }
}

// AC-4: Rate limited requests increment currency_service_rate_limited_requests_total metric
TEST(CurrencyRateLimitTest, test_ac4_rate_limit_metric_incremented) {
  auto stub = CreateStub();
  ConversionRequest req = CreateTestRequest();

  // Exceed rate limit
  for (int i = 0; i < kDefaultRPM + 5; ++i) {
    ClientContext ctx;
    ConversionResponse resp;
    stub->Convert(&ctx, req, &resp);
  }

  // Fetch metrics from OpenTelemetry collector / metrics endpoint
  auto metrics_provider = opentelemetry::metrics::Provider::GetMeterProvider();
  auto meter = metrics_provider->GetMeter("currency-test");
  // Verify counter exists with correct labels
  // Expected labels: client_ip (should be 127.0.0.1 for local test), method: "Convert"
  // Expected value: 5 (number of rejected requests)
  FAIL() << "Metric verification pending implementation (this test will fail until rate limiting is added)";
}

// AC-5: Different client IPs are rate limited independently
TEST(CurrencyRateLimitTest, test_ac5_independent_rate_limit_per_ip) {
  // Note: This test requires setup of multiple client IPs (e.g. using network namespaces or proxy)
  // Client 1: exceed limit, Client 2: should still be able to make requests
  FAIL() << "Multi-IP rate limit test pending test infrastructure setup (will fail until implementation exists)";
}

// AC-6: Status message includes correct configured rate limit value
TEST(CurrencyRateLimitTest, test_ac6_rate_limit_status_message) {
  auto stub = CreateStub();
  ConversionRequest req = CreateTestRequest();
  Status rate_limit_status;

  // Exceed limit to get rate limited response
  for (int i = 0; i < kDefaultRPM + 1; ++i) {
    ClientContext ctx;
    ConversionResponse resp;
    Status s = stub->Convert(&ctx, req, &resp);
    if (!s.ok() && s.error_code() == grpc::StatusCode::RESOURCE_EXHAUSTED) {
      rate_limit_status = s;
      break;
    }
  }

  ASSERT_EQ(rate_limit_status.error_code(), grpc::StatusCode::RESOURCE_EXHAUSTED);
  std::string expected_message = kRateLimitExceededMessagePrefix + std::to_string(kDefaultRPM) + kRateLimitExceededMessageSuffix;
  EXPECT_EQ(rate_limit_status.error_message(), expected_message);
}

}  // namespace

int main(int argc, char **argv) {
  testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
