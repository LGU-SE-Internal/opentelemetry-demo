#include <gtest/gtest.h>
#include <grpcpp/grpcpp.h>
#include <chrono>
#include <thread>
#include <csignal>
#include <cstdlib>
#include <memory>
#include <string>

#include "src/genproto/demo/currency_service.grpc.pb.h"

using opentelemetry::proto::demo::currency::v1::CurrencyService;
using opentelemetry::proto::demo::currency::v1::ConvertRequest;
using opentelemetry::proto::demo::currency::v1::ConvertResponse;
using opentelemetry::proto::demo::currency::v1::GetSupportedCurrenciesRequest;
using opentelemetry::proto::demo::currency::v1::GetSupportedCurrenciesResponse;
using grpc::Channel;
using grpc::ClientContext;
using grpc::Status;

namespace {
  const std::string kServiceAddress = "localhost:8082";
  const int kDefaultGracePeriod = 30;

  std::unique_ptr<CurrencyService::Stub> CreateClient() {
    auto channel = grpc::CreateChannel(kServiceAddress, grpc::InsecureChannelCredentials());
    return CurrencyService::NewStub(channel);
  }

  void RunLongConvertRequest(std::atomic<bool>* completed, std::atomic<bool>* failed) {
    auto stub = CreateClient();
    ConvertRequest req;
    req.mutable_from()->set_currency_code("USD");
    req.mutable_from()->set_units(100);
    req.set_to_code("EUR");
    ClientContext ctx;
    ConvertResponse resp;
    Status status = stub->Convert(&ctx, req, &resp);
    if (status.ok()) {
      *completed = true;
    } else {
      *failed = true;
    }
  }
}

// AC-1: New gRPC connections get UNAVAILABLE after shutdown signal
TEST(GracefulShutdownTest, test_ac1_new_connections_unavailable_after_signal) {
  // Start server as separate process, wait for it to be up
  // Send SIGTERM to server process
  // Try to create new connection and send request
  // Assert status code is UNAVAILABLE
  GTEST_SKIP() << "Implementation not present";
}

// AC-2: In-flight requests complete within grace period
TEST(GracefulShutdownTest, test_ac2_in_flight_requests_complete_within_grace_period) {
  // Start server with 10s grace period env var
  // Start a long-running request (mock delay on server side)
  // Send SIGTERM to server after 1s
  // Wait 2s, assert request completed successfully
  // Assert server exited with code 0
  GTEST_SKIP() << "Implementation not present";
}

// AC-3: Requests terminated after grace period elapses
TEST(GracefulShutdownTest, test_ac3_requests_force_terminated_after_grace_period) {
  // Start server with 2s grace period env var
  // Start a long-running request that takes 5s to complete
  // Send SIGTERM to server immediately after request starts
  // Wait 3s, assert request failed
  // Assert server exited with code 1
  GTEST_SKIP() << "Implementation not present";
}

// AC-4: Custom grace period duration from env var works
TEST(GracefulShutdownTest, test_ac4_custom_grace_period_from_env_var) {
  // Start server with GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS=10
  // Start a request that takes 8s
  // Send SIGTERM immediately
  // Wait 9s, assert request succeeded, server exited 0
  // Restart server with GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS=5
  // Start request that takes 8s
  // Send SIGTERM immediately
  // Wait 6s, assert request failed, server exited 1
  GTEST_SKIP() << "Implementation not present";
}

// AC-5: Grace period 0 causes immediate shutdown
TEST(GracefulShutdownTest, test_ac5_zero_grace_period_immediate_shutdown) {
  // Start server with GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS=0
  // Start a request
  // Send SIGTERM immediately
  // Assert request failed immediately
  // Assert server exited immediately with code 1
  GTEST_SKIP() << "Implementation not present";
}

// AC-6: Unit test verifies graceful shutdown behavior
TEST(GracefulShutdownTest, test_ac6_unit_test_verifies_shutdown_behavior) {
  // This test is covered by the unit test implementation requirement
  // This integration test confirms the unit test exists and passes
  GTEST_SKIP() << "Implementation not present";
}

int main(int argc, char **argv) {
  testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
