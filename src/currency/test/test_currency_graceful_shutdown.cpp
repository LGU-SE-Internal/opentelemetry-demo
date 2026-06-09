#include <gtest/gtest.h>
#include <chrono>
#include <csignal>
#include <cstdlib>
#include <thread>
#include <fstream>
#include <string>
#include <grpcpp/grpcpp.h>
#include "currency.pb.h"
#include "currency.grpc.pb.h"
#include <sys/wait.h>

namespace oteldemo {
namespace test {

using namespace std::chrono_literals;

class CurrencyServiceShutdownTest : public ::testing::Test {
protected:
    void SetUp() override {
        // Reset environment variable before each test
        unsetenv("CURRENCY_SERVICE_SHUTDOWN_TIMEOUT_SEC");
        // Clean up previous log
        std::remove("test_shutdown.log");
    }

    void TearDown() override {
        unsetenv("CURRENCY_SERVICE_SHUTDOWN_TIMEOUT_SEC");
        std::remove("test_shutdown.log");
    }

    std::unique_ptr<currency::CurrencyService::Stub> CreateStub(const std::string& addr = "localhost:8080") {
        auto channel = grpc::CreateChannel(addr, grpc::InsecureChannelCredentials());
        return currency::CurrencyService::NewStub(channel);
    }

    int RunServiceWithEnv(const std::string& env_var = "", int wait_ms = 2000) {
        std::string cmd = env_var + " ./src/currency/build/currencyservice > test_shutdown.log 2>&1 & echo $!";
        FILE* pipe = popen(cmd.c_str(), "r");
        if (!pipe) return -1;
        char pid_buf[32] = {0};
        fgets(pid_buf, sizeof(pid_buf), pipe);
        pclose(pipe);
        int pid = std::stoi(pid_buf);
        
        // Wait for service to start
        std::this_thread::sleep_for(std::chrono::milliseconds(wait_ms));
        return pid;
    }

    int WaitForProcessExit(int pid, int timeout_sec = 15) {
        int status;
        auto start = std::chrono::steady_clock::now();
        while (std::chrono::duration_cast<std::chrono::seconds>(std::chrono::steady_clock::now() - start).count() < timeout_sec) {
            int result = waitpid(pid, &status, WNOHANG);
            if (result == pid) {
                if (WIFEXITED(status)) {
                    return WEXITSTATUS(status);
                } else if (WIFSIGNALED(status)) {
                    return 128 + WTERMSIG(status);
                }
                return -1;
            }
            std::this_thread::sleep_for(100ms);
        }
        kill(pid, SIGKILL);
        waitpid(pid, &status, 0);
        return -2; // timeout
    }

    bool LogContains(const std::string& substring) {
        std::ifstream log_file("test_shutdown.log");
        std::string line;
        while (std::getline(log_file, line)) {
            if (line.find(substring) != std::string::npos) {
                return true;
            }
        }
        return false;
    }
};

// AC-1: When SIGINT/SIGTERM sent with no active requests, exit within 1s with code 0, no errors
TEST_F(CurrencyServiceShutdownTest, test_ac1_shutdown_no_active_requests_sigint) {
    int pid = RunServiceWithEnv();
    ASSERT_GT(pid, 0);
    
    auto start = std::chrono::steady_clock::now();
    kill(pid, SIGINT);
    int exit_code = WaitForProcessExit(pid, 2);
    auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start);
    
    EXPECT_EQ(exit_code, 0);
    EXPECT_LT(duration.count(), 1000);
    EXPECT_FALSE(LogContains("ERROR"));
}

TEST_F(CurrencyServiceShutdownTest, test_ac1_shutdown_no_active_requests_sigterm) {
    int pid = RunServiceWithEnv();
    ASSERT_GT(pid, 0);
    
    auto start = std::chrono::steady_clock::now();
    kill(pid, SIGTERM);
    int exit_code = WaitForProcessExit(pid, 2);
    auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start);
    
    EXPECT_EQ(exit_code, 0);
    EXPECT_LT(duration.count(), 1000);
    EXPECT_FALSE(LogContains("ERROR"));
}

// AC-2: When SIGINT/SIGTERM sent during in-flight request that completes within timeout, request succeeds, exit code 0
TEST_F(CurrencyServiceShutdownTest, test_ac2_shutdown_during_successful_request_sigint) {
    int pid = RunServiceWithEnv();
    ASSERT_GT(pid, 0);
    auto stub = CreateStub();
    
    // Start slow conversion request (simulated delay)
    std::atomic<bool> request_succeeded = false;
    std::thread request_thread([&stub, &request_succeeded]() {
        grpc::ClientContext ctx;
        currency::ConversionRequest req;
        req.set_from_code("USD");
        req.set_to_code("EUR");
        req.set_units(100);
        req.set_nanos(0);
        currency::ConversionResponse resp;
        grpc::Status status = stub->Convert(&ctx, req, &resp);
        request_succeeded = status.ok();
    });
    
    // Wait a bit for request to be in flight
    std::this_thread::sleep_for(500ms);
    kill(pid, SIGINT);
    
    request_thread.join();
    int exit_code = WaitForProcessExit(pid, 5);
    EXPECT_TRUE(request_succeeded);
    EXPECT_EQ(exit_code, 0);
}

// AC-3: When SIGINT/SIGTERM sent during request exceeding timeout, request cancelled, exit code 130/143
TEST_F(CurrencyServiceShutdownTest, test_ac3_shutdown_timeout_request_sigint) {
    // Set very short timeout
    int pid = RunServiceWithEnv("CURRENCY_SERVICE_SHUTDOWN_TIMEOUT_SEC=1");
    ASSERT_GT(pid, 0);
    auto stub = CreateStub();
    
    // Start request that takes longer than 1s
    std::atomic<bool> request_failed = false;
    std::thread request_thread([&stub, &request_failed]() {
        grpc::ClientContext ctx;
        ctx.set_deadline(std::chrono::system_clock::now() + 3s);
        currency::ConversionRequest req;
        req.set_from_code("USD");
        req.set_to_code("EUR");
        req.set_units(100);
        req.set_nanos(0);
        // Add simulated delay header to make request take 2s
        ctx.AddMetadata("x-simulate-delay-ms", "2000");
        currency::ConversionResponse resp;
        grpc::Status status = stub->Convert(&ctx, req, &resp);
        request_failed = !status.ok();
    });
    
    std::this_thread::sleep_for(500ms);
    kill(pid, SIGINT);
    
    request_thread.join();
    int exit_code = WaitForProcessExit(pid, 3);
    EXPECT_TRUE(request_failed);
    EXPECT_EQ(exit_code, 130);
}

TEST_F(CurrencyServiceShutdownTest, test_ac3_shutdown_timeout_request_sigterm) {
    int pid = RunServiceWithEnv("CURRENCY_SERVICE_SHUTDOWN_TIMEOUT_SEC=1");
    ASSERT_GT(pid, 0);
    auto stub = CreateStub();
    
    std::atomic<bool> request_failed = false;
    std::thread request_thread([&stub, &request_failed]() {
        grpc::ClientContext ctx;
        ctx.set_deadline(std::chrono::system_clock::now() + 3s);
        currency::ConversionRequest req;
        req.set_from_code("USD");
        req.set_to_code("EUR");
        req.set_units(100);
        req.set_nanos(0);
        ctx.AddMetadata("x-simulate-delay-ms", "2000");
        currency::ConversionResponse resp;
        grpc::Status status = stub->Convert(&ctx, req, &resp);
        request_failed = !status.ok();
    });
    
    std::this_thread::sleep_for(500ms);
    kill(pid, SIGTERM);
    
    request_thread.join();
    int exit_code = WaitForProcessExit(pid, 3);
    EXPECT_TRUE(request_failed);
    EXPECT_EQ(exit_code, 143);
}

// AC-4: Valid CURRENCY_SERVICE_SHUTDOWN_TIMEOUT_SEC uses configured value
TEST_F(CurrencyServiceShutdownTest, test_ac4_configured_timeout_used) {
    const int custom_timeout = 5;
    int pid = RunServiceWithEnv("CURRENCY_SERVICE_SHUTDOWN_TIMEOUT_SEC=" + std::to_string(custom_timeout));
    ASSERT_GT(pid, 0);
    auto stub = CreateStub();
    
    std::atomic<bool> request_succeeded = false;
    std::thread request_thread([&stub, &request_succeeded]() {
        grpc::ClientContext ctx;
        ctx.set_deadline(std::chrono::system_clock::now() + 6s);
        currency::ConversionRequest req;
        req.set_from_code("USD");
        req.set_to_code("EUR");
        req.set_units(100);
        req.set_nanos(0);
        ctx.AddMetadata("x-simulate-delay-ms", "3000"); // 3s, less than 5s timeout
        currency::ConversionResponse resp;
        grpc::Status status = stub->Convert(&ctx, req, &resp);
        request_succeeded = status.ok();
    });
    
    std::this_thread::sleep_for(500ms);
    kill(pid, SIGTERM);
    
    auto start = std::chrono::steady_clock::now();
    request_thread.join();
    int exit_code = WaitForProcessExit(pid, 7);
    auto duration = std::chrono::duration_cast<std::chrono::seconds>(std::chrono::steady_clock::now() - start);
    
    EXPECT_TRUE(request_succeeded);
    EXPECT_EQ(exit_code, 0);
    // Should take ~3s, definitely less than 10s default
    EXPECT_LT(duration.count(), 7);
}

// AC-5: Invalid timeout values fall back to default 10s with warning log
TEST_F(CurrencyServiceShutdownTest, test_ac5_invalid_timeout_fallback_negative) {
    int pid = RunServiceWithEnv("CURRENCY_SERVICE_SHUTDOWN_TIMEOUT_SEC=-5");
    ASSERT_GT(pid, 0);
    
    kill(pid, SIGTERM);
    int exit_code = WaitForProcessExit(pid, 2);
    
    EXPECT_EQ(exit_code, 0);
    EXPECT_TRUE(LogContains("WARNING"));
    EXPECT_TRUE(LogContains("CURRENCY_SERVICE_SHUTDOWN_TIMEOUT_SEC"));
    EXPECT_TRUE(LogContains("default 10s"));
}

TEST_F(CurrencyServiceShutdownTest, test_ac5_invalid_timeout_fallback_non_integer) {
    int pid = RunServiceWithEnv("CURRENCY_SERVICE_SHUTDOWN_TIMEOUT_SEC=invalid");
    ASSERT_GT(pid, 0);
    
    kill(pid, SIGTERM);
    int exit_code = WaitForProcessExit(pid, 2);
    
    EXPECT_EQ(exit_code, 0);
    EXPECT_TRUE(LogContains("WARNING"));
    EXPECT_TRUE(LogContains("CURRENCY_SERVICE_SHUTDOWN_TIMEOUT_SEC"));
    EXPECT_TRUE(LogContains("default 10s"));
}

TEST_F(CurrencyServiceShutdownTest, test_ac5_invalid_timeout_fallback_zero) {
    int pid = RunServiceWithEnv("CURRENCY_SERVICE_SHUTDOWN_TIMEOUT_SEC=0");
    ASSERT_GT(pid, 0);
    
    kill(pid, SIGTERM);
    int exit_code = WaitForProcessExit(pid, 2);
    
    EXPECT_EQ(exit_code, 0);
    EXPECT_TRUE(LogContains("WARNING"));
    EXPECT_TRUE(LogContains("CURRENCY_SERVICE_SHUTDOWN_TIMEOUT_SEC"));
    EXPECT_TRUE(LogContains("default 10s"));
}

// AC-6: Rates refresh thread stops immediately on shutdown, no new refreshes started
TEST_F(CurrencyServiceShutdownTest, test_ac6_rates_refresh_stops_on_shutdown) {
    int pid = RunServiceWithEnv();
    ASSERT_GT(pid, 0);
    
    // Wait for at least one rate refresh to happen
    std::this_thread::sleep_for(2s);
    // Count refresh logs before shutdown
    int refresh_count_before = 0;
    std::ifstream log_before("test_shutdown.log");
    std::string line;
    while (std::getline(log_before, line)) {
        if (line.find("Refreshing exchange rates") != std::string::npos) {
            refresh_count_before++;
        }
    }
    
    kill(pid, SIGTERM);
    std::this_thread::sleep_for(3s); // Wait for potential refresh intervals
    WaitForProcessExit(pid, 5);
    
    // Count refresh logs after shutdown initiation
    int refresh_count_after = 0;
    std::ifstream log_after("test_shutdown.log");
    while (std::getline(log_after, line)) {
        if (line.find("Refreshing exchange rates") != std::string::npos) {
            refresh_count_after++;
        }
    }
    
    // No new refreshes after shutdown was triggered
    EXPECT_EQ(refresh_count_after, refresh_count_before);
}

} // namespace test
} // namespace oteldemo

int main(int argc, char** argv) {
    ::testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}
