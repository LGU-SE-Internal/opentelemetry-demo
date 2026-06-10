// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

#include <cstdlib>
#include <iostream>
#include <math.h>
#include <csignal>
#include <demo.grpc.pb.h>
#include <grpc/health/v1/health.grpc.pb.h>
#include <thread>
#include <atomic>
#include "httplib.h"
#include <shared_mutex>
#include <nlohmann/json.hpp>

#include "opentelemetry/trace/context.h"
#include "opentelemetry/semconv/incubating/rpc_attributes.h"
#include "opentelemetry/trace/span_context_kv_iterable_view.h"
#include "opentelemetry/baggage/baggage.h"
#include "opentelemetry/nostd/string_view.h"
#include "logger_common.h"
#include "meter_common.h"
#include "tracer_common.h"

#include <grpcpp/grpcpp.h>
#include <grpcpp/server.h>
#include <grpcpp/server_builder.h>
#include <grpcpp/server_context.h>
#include <grpcpp/impl/codegen/string_ref.h>
#include <grpcpp/security/credentials.h>

#include <fstream>
#include <cerrno>
#include <cstring>
#include <algorithm>
#include <cctype>

using namespace std;
using namespace opentelemetry::baggage;
using namespace opentelemetry::trace;

using oteldemo::Empty;
using oteldemo::GetSupportedCurrenciesResponse;
using oteldemo::CurrencyConversionRequest;
using oteldemo::Money;

using grpc::Status;
using grpc::ServerContext;
using grpc::ServerBuilder;
using grpc::Server;

using Span            = Span;
using SpanContext     = SpanContext;
namespace context     = opentelemetry::context;
namespace metrics_api = opentelemetry::metrics;
namespace nostd       = opentelemetry::nostd;
namespace semconv     = opentelemetry::semconv;

namespace
{
  // Shutdown related globals
  std::atomic<bool> g_shutdown_initiated{false};
  std::atomic<int> g_received_signal{0};
  std::chrono::seconds g_shutdown_timeout{10};
  std::shared_ptr<Server> g_server;
  std::unique_ptr<httplib::Server> g_http_server;
  std::atomic<bool> g_is_healthy{true};
  std::atomic<bool> g_is_ready{false};

  // Hardcoded default rates
  const std::unordered_map<std::string, double> HARDCODED_DEFAULT_RATES = {
    {"EUR", 1.0},
    {"USD", 1.1305},
    {"JPY", 126.40},
    {"BGN", 1.9558},
    {"CZK", 25.592},
    {"DKK", 7.4609},
    {"GBP", 0.85970},
    {"HUF", 315.51},
    {"PLN", 4.2996},
    {"RON", 4.7463},
    {"SEK", 10.5375},
    {"CHF", 1.1360},
    {"ISK", 136.80},
    {"NOK", 9.8040},
    {"HRK", 7.4210},
    {"RUB", 74.4208},
    {"TRY", 6.1247},
    {"AUD", 1.6072},
    {"BRL", 4.2682},
    {"CAD", 1.5128},
    {"CNY", 7.5857},
    {"HKD", 8.8743},
    {"IDR", 15999.40},
    {"ILS", 4.0875},
    {"INR", 79.4320},
    {"KRW", 1275.05},
    {"MXN", 21.7999},
    {"MYR", 4.6289},
    {"NZD", 1.6679},
    {"PHP", 59.083},
    {"SGD", 1.5349},
    {"THB", 36.012},
    {"ZAR", 16.0583},
  };

  // Thread-safe rate storage
  std::unordered_map<std::string, double> current_rates = HARDCODED_DEFAULT_RATES;
  mutable std::shared_mutex rates_mutex;

  // Background refresh thread variables
  std::thread refresh_thread;
  std::atomic<bool> refresh_running{false};
  std::string configured_rates_file_path;

  std::string version = std::getenv("VERSION"); 
  std::string name{ "currency" };

  nostd::unique_ptr<metrics_api::Counter<uint64_t>> currency_counter;
  nostd::shared_ptr<opentelemetry::logs::Logger> logger;

  // Rate limiting configuration
  int g_rate_limit_rps = 0;
  nostd::unique_ptr<metrics_api::Counter<uint64_t>> g_rate_limited_counter;

  // Thread-safe token bucket rate limiter
  class TokenBucket {
  public:
    explicit TokenBucket(int max_tokens) : max_tokens_(max_tokens), tokens_(max_tokens), last_refill_(std::chrono::steady_clock::now()) {}

    bool try_consume() {
      std::lock_guard<std::mutex> lock(mutex_);
      refill_tokens();
      if (tokens_ >= 1) {
        tokens_--;
        return true;
      }
      return false;
    }

  private:
    void refill_tokens() {
      auto now = std::chrono::steady_clock::now();
      auto duration = std::chrono::duration_cast<std::chrono::seconds>(now - last_refill_);
      if (duration.count() >= 1) {
        tokens_ = max_tokens_;
        last_refill_ = now;
      }
    }

    int max_tokens_;
    int tokens_;
    std::chrono::steady_clock::time_point last_refill_;
    std::mutex mutex_;
  };

  std::unordered_map<std::string, std::unique_ptr<TokenBucket>> g_rate_limiters;
  std::shared_mutex g_rate_limiters_mutex;

  // Extract client IP from gRPC peer string
  std::string extract_client_ip(const std::string& peer) {
    size_t ipv4_pos = peer.find("ipv4:");
    if (ipv4_pos != std::string::npos) {
      size_t ip_end = peer.find(':', ipv4_pos + 5);
      if (ip_end != std::string::npos) {
        return peer.substr(ipv4_pos + 5, ip_end - (ipv4_pos + 5));
      }
    }
    size_t ipv6_pos = peer.find("ipv6:");
    if (ipv6_pos != std::string::npos) {
      size_t ip_end = peer.find(']', ipv6_pos + 5);
      if (ip_end != std::string::npos) {
        return peer.substr(ipv6_pos + 5, ip_end - (ipv6_pos + 5));
      }
    }
    return peer; // Fallback to full peer string if parsing fails
  }

  // Rate limiting gRPC interceptor
  class RateLimitInterceptor : public grpc::experimental::Interceptor {
  public:
    void Intercept(grpc::experimental::InterceptorBatchMethods* methods) override {
      if (methods->QueryInterceptionHookPoint(grpc::experimental::InterceptionHookPoints::PRE_SEND_INITIAL_METADATA)) {
        auto* context = methods->GetServerContext();
        std::string peer = context->peer();
        std::string client_ip = extract_client_ip(peer);
        std::string method_name = context->method();

        // Skip rate limiting if disabled
        if (g_rate_limit_rps <= 0) {
          methods->Proceed();
          return;
        }

        // Get or create rate limiter for this IP
        std::shared_lock<std::shared_mutex> read_lock(g_rate_limiters_mutex);
        auto it = g_rate_limiters.find(client_ip);
        if (it == g_rate_limiters.end()) {
          read_lock.unlock();
          std::unique_lock<std::shared_mutex> write_lock(g_rate_limiters_mutex);
          // Check again after acquiring write lock to avoid race
          it = g_rate_limiters.find(client_ip);
          if (it == g_rate_limiters.end()) {
            it = g_rate_limiters.emplace(client_ip, std::make_unique<TokenBucket>(g_rate_limit_rps)).first;
          }
        }

        if (!it->second->try_consume()) {
          // Rate limit exceeded
          std::string error_msg = "Rate limit exceeded: maximum " + std::to_string(g_rate_limit_rps) + " requests per second per IP address";
          methods->ModifySendStatus(grpc::Status(grpc::StatusCode::RESOURCE_EXHAUSTED, error_msg));
          
          // Increment metric
          std::map<std::string, std::string> labels = {
            {"ip_address", client_ip},
            {"endpoint", method_name}
          };
          g_rate_limited_counter->Add(1, labels);
          return;
        }

        methods->Proceed();
      } else {
        methods->Proceed();
      }
    }
  };

  class RateLimitInterceptorFactory : public grpc::experimental::ServerInterceptorFactoryInterface {
  public:
    grpc::experimental::Interceptor* CreateServerInterceptor(grpc::experimental::ServerRpcInfo* info) override {
      return new RateLimitInterceptor();
    }
  };

  // Public interface function implementations
  std::unordered_map<std::string, double> get_current_rates() {
    std::shared_lock<std::shared_mutex> lock(rates_mutex);
    return current_rates;
  }

  std::unordered_map<std::string, double> apply_env_overrides(std::unordered_map<std::string, double> base_rates) {
    extern char** environ;
    for (char** env = environ; *env != nullptr; ++env) {
      std::string env_str(*env);
      if (env_str.starts_with("CURRENCY_RATE_")) {
        size_t eq_pos = env_str.find('=');
        if (eq_pos == std::string::npos) continue;

        std::string var_name = env_str.substr(0, eq_pos);
        std::string var_value = env_str.substr(eq_pos + 1);
        std::string currency_code = var_name.substr(15); // Length of "CURRENCY_RATE_"

        // Convert to uppercase
        std::transform(currency_code.begin(), currency_code.end(), currency_code.begin(),
                       [](unsigned char c) { return std::toupper(c); });

        try {
          double rate = std::stod(var_value);
          base_rates[currency_code] = rate;
          logger->Info("Applied environment override for " + currency_code + ": " + std::to_string(rate));
        } catch (const std::exception& e) {
          logger->Error("Failed to parse environment override for " + currency_code + ": value '" + var_value + "' is not a valid number");
        }
      }
    }
    return base_rates;
  }

  bool load_rates_from_file(const std::string& file_path) {
    try {
      std::ifstream file(file_path);
      if (!file.is_open()) {
        logger->Error("Failed to open rates file: " + file_path);
        return false;
      }

      nlohmann::json j;
      file >> j;

      if (!j.contains("rates") || !j["rates"].is_object()) {
        logger->Error("Invalid rates file format: missing or invalid 'rates' object");
        return false;
      }

      std::unordered_map<std::string, double> new_rates = HARDCODED_DEFAULT_RATES;

      for (auto& [code, rate_val] : j["rates"].items()) {
        try {
          double rate = rate_val.get<double>();
          new_rates[code] = rate;
        } catch (const nlohmann::json::exception& e) {
          logger->Error("Invalid rate value for currency " + code + " in file: " + e.what());
        }
      }

      // Apply environment overrides
      new_rates = apply_env_overrides(new_rates);

      // Atomic update
      {
        std::unique_lock<std::shared_mutex> lock(rates_mutex);
        current_rates.swap(new_rates);
      }

      logger->Info("Successfully loaded rates from file: " + file_path);
      return true;
    } catch (const nlohmann::json::parse_error& e) {
      logger->Error("Failed to parse rates file: " + std::string(e.what()));
      return false;
    } catch (const std::exception& e) {
      logger->Error("Error loading rates from file: " + std::string(e.what()));
      return false;
    }
  }

  void InitiateGracefulShutdown() noexcept {
  if (g_shutdown_initiated.exchange(true)) {
    return; // Already shutting down
  }
  logger->Info("Shutdown initiated, waiting up to 10s for in-flight requests to complete");
  // Stop refresh thread first
  refresh_running = false;
  // Initiate gRPC server shutdown
  if (g_server) {
    g_server->Shutdown(std::chrono::system_clock::now() + g_shutdown_timeout);
  }
}

bool WaitForShutdownComplete(std::chrono::seconds timeout) noexcept {
  auto start = std::chrono::steady_clock::now();
  // Wait for refresh thread to join
  if (refresh_thread.joinable()) {
    refresh_thread.join();
  }
  // Flush all telemetry
  forceFlushTracer(std::chrono::seconds(1));
  forceFlushMeter(std::chrono::seconds(1));
  forceFlushLogger(std::chrono::seconds(1));
  // Return true if we completed before timeout
  return std::chrono::duration_cast<std::chrono::seconds>(std::chrono::steady_clock::now() - start) < timeout;
}

void SignalHandler(int signal) {
  g_received_signal = signal;
  InitiateGracefulShutdown();
}

void RegisterShutdownSignalHandlers() {
  struct sigaction action;
  action.sa_handler = SignalHandler;
  sigemptyset(&action.sa_mask);
  action.sa_flags = 0;
  sigaction(SIGINT, &action, nullptr);
  sigaction(SIGTERM, &action, nullptr);
}

void start_refresh_loop(int interval_seconds) {
    if (interval_seconds < 10) {
      logger->Warning("Refresh interval " + std::to_string(interval_seconds) + "s is below minimum 10s, using 10s");
      interval_seconds = 10;
    }

    if (refresh_running.exchange(true)) {
      logger->Warning("Refresh loop already running, ignoring start request");
      return;
    }

    configured_rates_file_path = std::getenv("CURRENCY_RATES_FILE") ? std::getenv("CURRENCY_RATES_FILE") : "";

    refresh_thread = std::thread([interval_seconds]() {
      while (refresh_running.load()) {
        std::this_thread::sleep_for(std::chrono::seconds(interval_seconds));
        if (!configured_rates_file_path.empty()) {
          logger->Info("Refreshing rates from file: " + configured_rates_file_path);
          load_rates_from_file(configured_rates_file_path);
        }
      }
    });
  }

  // Helper for test AC-9
  std::string convert_currency(double amount, const std::string& from_code, const std::string& to_code) {
    std::shared_lock<std::shared_mutex> lock(rates_mutex);
    double from_rate = current_rates.at(from_code);
    double to_rate = current_rates.at(to_code);
    double result = (amount / from_rate) * to_rate;
    return std::to_string(result);
  }

class HealthServer final : public grpc::health::v1::Health::Service
{
  Status Check(
    ServerContext* context,
    const grpc::health::v1::HealthCheckRequest* request,
    grpc::health::v1::HealthCheckResponse* response) override
  {
    response->set_status(grpc::health::v1::HealthCheckResponse::SERVING);
    return Status::OK;
  }
};

class CurrencyService final : public oteldemo::CurrencyService::Service
{
  Status GetSupportedCurrencies(ServerContext* context,
  	const Empty* request,
  	GetSupportedCurrenciesResponse* response) override
  {
    StartSpanOptions options;
    options.kind = SpanKind::kServer;
    GrpcServerCarrier carrier(context);

    auto prop        = context::propagation::GlobalTextMapPropagator::GetGlobalPropagator();
    auto current_ctx = context::RuntimeContext::GetCurrent();
    auto new_context = prop->Extract(carrier, current_ctx);
    options.parent   = GetSpan(new_context)->GetContext();

    std::string span_name = "Currency/GetSupportedCurrencies";
    auto span =
        get_tracer("currency")->StartSpan(span_name,
                                      {{semconv::rpc::kRpcSystem, "grpc"},
                                       {semconv::rpc::kRpcService, "oteldemo.CurrencyService"},
                                       {semconv::rpc::kRpcMethod, "GetSupportedCurrencies"},
                                       {semconv::rpc::kRpcGrpcStatusCode, semconv::rpc::RpcGrpcStatusCodeValues::kOk}},
                                      options);
    auto scope = get_tracer("currency")->WithActiveSpan(span);

    span->AddEvent("Processing supported currencies request");

    for (auto &code : get_current_rates()) {
      response->add_currency_codes(code.first);
    }

    span->AddEvent("Currencies fetched, response sent back");
    span->SetStatus(StatusCode::kOk);

    logger->Info(std::string(__func__) + " successful");

    // Make sure to end your spans!
    span->End();
  	return Status::OK;
  }

  double getDouble(Money& money) {
    auto units = money.units();
    auto nanos = money.nanos();

    double decimal = 0.0;
    while (nanos != 0) {
      double t = (double)(nanos%10)/10;
      nanos = nanos/10;
      decimal = decimal/10 + t;
    }

    return double(units) + decimal;
  }

  void getUnitsAndNanos(Money& money, double value) {
    long unit = (long)value;
    double rem = value - unit;
    long nano = rem * pow(10, 9);
    money.set_units(unit);
    money.set_nanos(nano);
  }

  Status Convert(ServerContext* context,
  	const CurrencyConversionRequest* request,
  	Money* response) override
  {
    StartSpanOptions options;
    options.kind = SpanKind::kServer;
    GrpcServerCarrier carrier(context);

    auto prop        = context::propagation::GlobalTextMapPropagator::GetGlobalPropagator();
    auto current_ctx = context::RuntimeContext::GetCurrent();
    auto new_context = prop->Extract(carrier, current_ctx);
    options.parent   = GetSpan(new_context)->GetContext();

    std::string span_name = "Currency/Convert";
    auto span =
        get_tracer("currency")->StartSpan(span_name,
                                      {{semconv::rpc::kRpcSystem, "grpc"},
                                       {semconv::rpc::kRpcService, "oteldemo.CurrencyService"},
                                       {semconv::rpc::kRpcMethod, "Convert"},
                                       {semconv::rpc::kRpcGrpcStatusCode, semconv::rpc::RpcGrpcStatusCodeValues::kOk}},
                                      options);
    auto scope = get_tracer("currency")->WithActiveSpan(span);

    span->AddEvent("Processing currency conversion request");

    try {
      // Do the conversion work
      Money from = request->from();
      string from_code = from.currency_code();
      
      // Validate amount units are non-negative
      if (from.units() < 0) {
        span->SetStatus(StatusCode::kError);
        logger->Error(std::string(__func__) + " conversion failed: amount units cannot be negative");
        span->End();
        return Status(grpc::INVALID_ARGUMENT, "amount cannot be negative");
      }
      
      // Validate amount nanos are non-negative
      if (from.nanos() < 0) {
        span->SetStatus(StatusCode::kError);
        logger->Error(std::string(__func__) + " conversion failed: amount nanos cannot be negative");
        span->End();
        return Status(grpc::INVALID_ARGUMENT, "amount cannot be negative");
      }
      
      // Validate from currency code is not empty
      if (from_code.empty()) {
        span->SetStatus(StatusCode::kError);
        logger->Error(std::string(__func__) + " conversion failed: from currency code is empty");
        span->End();
        return Status(grpc::INVALID_ARGUMENT, "from_currency cannot be empty");
      }
      
      std::shared_lock<std::shared_mutex> lock(rates_mutex);
      // Validate from currency code is supported
      if (current_rates.find(from_code) == current_rates.end()) {
        span->SetStatus(StatusCode::kError);
        logger->Error(std::string(__func__) + " conversion failed: unsupported from currency code: " + from_code);
        span->End();
        return Status(grpc::INVALID_ARGUMENT, "from_currency " + from_code + " is not supported");
      }
      double rate = current_rates.at(from_code);
      double one_euro = getDouble(from) / rate ;

      string to_code = request->to_code();
      
      // Validate to currency code is not empty
      if (to_code.empty()) {
        span->SetStatus(StatusCode::kError);
        logger->Error(std::string(__func__) + " conversion failed: to currency code is empty");
        span->End();
        return Status(grpc::INVALID_ARGUMENT, "to_currency cannot be empty");
      }
      
      // Validate to currency code is supported
      if (current_rates.find(to_code) == current_rates.end()) {
        span->SetStatus(StatusCode::kError);
        logger->Error(std::string(__func__) + " conversion failed: unsupported to currency code: " + to_code);
        span->End();
        return Status(grpc::INVALID_ARGUMENT, "to_currency " + to_code + " is not supported");
      }
      double to_rate = current_rates.at(to_code);

      double final = one_euro * to_rate;
      getUnitsAndNanos(*response, final);
      response->set_currency_code(to_code);

      span->SetAttribute("demo.exchange.from", from_code);
      span->SetAttribute("demo.exchange.to", to_code);

      CurrencyCounter(to_code);

      span->AddEvent("Conversion successful, response sent back");
      span->SetStatus(StatusCode::kOk);

      logger->Info(std::string(__func__) + " conversion successful");
      
      // End the span
      span->End();
      return Status::OK;

    } catch(...) {
      span->AddEvent("Conversion failed");
      span->SetStatus(StatusCode::kError);

      logger->Error(std::string(__func__) + " conversion failure");

      span->End();
      return Status::CANCELLED;
    }
    return Status::OK;
  }

  void CurrencyCounter(const std::string& currency_code)
  {
      std::map<std::string, std::string> labels = { {"currency_code", currency_code} };
      auto labelkv = common::KeyValueIterableView<decltype(labels)>{ labels };
      currency_counter->Add(1, labelkv);
  }
};

// Global server pointer for signal handler access
std::shared_ptr<Server> g_server;
std::unique_ptr<httplib::Server> g_http_server;
std::atomic<bool> g_is_healthy{true};
std::atomic<bool> g_is_ready{false};
std::atomic<bool> g_shutdown_initiated{false};
std::atomic<bool> g_shutdown_timed_out{false};

// Initiates graceful shutdown sequence:
// 1. Logs shutdown start event
// 2. Stops accepting new gRPC connections
// 3. Waits up to 10s for in-flight requests to complete
// 4. Logs shutdown completion event and exits
void PerformGracefulShutdown(std::shared_ptr<grpc::Server> server) {
  if (g_shutdown_initiated.exchange(true)) {
    // Shutdown already in progress
    return;
  }

  logger->Info("Graceful shutdown initiated, waiting up to 10s for in-flight requests to complete");

  // Stop health checks first
  g_is_healthy = false;
  g_is_ready = false;
  if (g_http_server) {
    g_http_server->stop();
  }

  if (!server) {
    logger->Info("Graceful shutdown completed, all in-flight requests processed");
    return;
  }

  // Initiate gRPC shutdown with 10s timeout
  gpr_timespec timeout = {10, 0, GPR_TIMESPAN};
  auto shutdown_start = std::chrono::steady_clock::now();
  server->Shutdown(timeout);

  // Check if shutdown completed before timeout
  auto shutdown_duration = std::chrono::duration_cast<std::chrono::seconds>(std::chrono::steady_clock::now() - shutdown_start);
  if (shutdown_duration.count() >= 10) {
    g_shutdown_timed_out = true;
    logger->Warning("Graceful shutdown timed out after 10s, terminating with pending requests");
  } else {
    logger->Info("Graceful shutdown completed, all in-flight requests processed");
  }
}

// Registers signal handlers for SIGINT and SIGTERM to trigger graceful shutdown
void RegisterShutdownSignalHandlers(std::shared_ptr<grpc::Server> server) {
  g_server = server;
  std::signal(SIGINT, [](int signal) {
    PerformGracefulShutdown(g_server);
  });
  std::signal(SIGTERM, [](int signal) {
    PerformGracefulShutdown(g_server);
  });
}

// Old signal handler kept for reference, will be removed
void SignalHandler(int signal) {
  g_is_healthy = false;
  g_is_ready = false;
  if (g_http_server) {
    g_http_server->stop();
  }
  if (g_server) {
    gpr_timespec timeout = {10, 0, GPR_TIMESPAN};
    g_server->Shutdown(timeout);
  }
}

// Helper function to read file content into string
std::string read_file(const std::string& path) {
  std::ifstream file(path);
  if (!file.is_open()) {
    return "";
  }
  return std::string((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
}

// Helper function to check if file exists and is readable
bool file_exists(const std::string& path) {
  std::ifstream f(path.c_str());
  return f.good();
}

void RunServer(uint16_t port)
{
  // Start HTTP health server first
  uint16_t health_port = 8081;
  const char* health_port_env = std::getenv("CURRENCY_SERVICE_HEALTH_PORT");
  if (health_port_env) {
    int p = atoi(health_port_env);
    if (p > 0 && p <= 65535) {
      health_port = static_cast<uint16_t>(p);
    }
  }
  
  g_http_server = std::make_unique<httplib::Server>();
  
  g_http_server->Get("/health", [](const httplib::Request&, httplib::Response& res) {
    if (g_is_healthy.load()) {
      res.status = 200;
    } else {
      res.status = 503;
    }
  });
  
  g_http_server->Get("/ready", [](const httplib::Request&, httplib::Response& res) {
    if (g_is_ready.load()) {
      res.status = 200;
    } else {
      res.status = 503;
    }
  });
  
  std::thread http_thread([health_port]() {
    g_http_server->listen("0.0.0.0", health_port);
  });

  logger->Info("HTTP health server listening on port: " + std::to_string(health_port));

  std::string ip("0.0.0.0");

  const char* ipv6_enabled = std::getenv("IPV6_ENABLED");
  
  if (ipv6_enabled == "true") {
    ip = "[::]";
    logger->Info("Overwriting Localhost IP: " + ip);
  }

  std::string address(ip + ":" +  std::to_string(port));

  CurrencyService currencyService;
  HealthServer healthService;
  ServerBuilder builder;

  // Read TLS configuration environment variables
  const char* tls_enabled_env = std::getenv("CURRENCY_SERVICE_TLS_ENABLED");
  bool tls_enabled = (tls_enabled_env != nullptr) && (std::string(tls_enabled_env) == "true");

  const char* mtls_enabled_env = std::getenv("CURRENCY_SERVICE_MTLS_ENABLED");
  bool mtls_enabled = (mtls_enabled_env != nullptr) && (std::string(mtls_enabled_env) == "true");

  const char* tls_cert_path = std::getenv("CURRENCY_SERVICE_TLS_CERT_PATH");
  const char* tls_key_path = std::getenv("CURRENCY_SERVICE_TLS_KEY_PATH");
  const char* tls_ca_path = std::getenv("CURRENCY_SERVICE_TLS_CA_CERT_PATH");
  
  std::shared_ptr<grpc::ServerCredentials> server_creds;
  
  if (tls_enabled) {
    // Validate TLS configuration
    if (tls_cert_path == nullptr || tls_key_path == nullptr) {
      logger->Error("Missing required TLS configuration: both certificate and private key paths must be set when TLS is enabled");
      exit(1);
    }
    
    if (mtls_enabled && tls_ca_path == nullptr) {
      logger->Error("Missing required mTLS configuration: CA certificate path must be set when mTLS is enabled");
      exit(1);
    }
    
    // Check all files exist
    if (!file_exists(tls_cert_path)) {
      logger->Error("TLS certificate file " + std::string(tls_cert_path) + " is missing or unreadable");
      exit(1);
    }
    if (!file_exists(tls_key_path)) {
      logger->Error("TLS private key file " + std::string(tls_key_path) + " is missing or unreadable");
      exit(1);
    }
    if (mtls_enabled && !file_exists(tls_ca_path)) {
      logger->Error("mTLS CA certificate file " + std::string(tls_ca_path) + " is missing or unreadable");
      exit(1);
    }
    
    // Read certificate files
    std::string cert_data = read_file(tls_cert_path);
    std::string key_data = read_file(tls_key_path);
    
    if (cert_data.empty() || key_data.empty()) {
      logger->Error("Invalid TLS certificate or private key: empty file content");
      exit(1);
    }
    
    grpc::SslServerCredentialsOptions ssl_opts;
    grpc::SslServerCredentialsOptions::PemKeyCertPair key_cert_pair;
    key_cert_pair.private_key = key_data;
    key_cert_pair.cert_chain = cert_data;
    ssl_opts.pem_key_cert_pairs.push_back(key_cert_pair);
    
    if (mtls_enabled) {
      // mTLS mode: require client certificate verification
      std::string ca_data = read_file(tls_ca_path);
      if (ca_data.empty()) {
        logger->Error("Invalid CA certificate: empty file content");
        exit(1);
      }
      ssl_opts.pem_root_certs = ca_data;
      ssl_opts.client_certificate_request = GRPC_SSL_REQUEST_AND_REQUIRE_CLIENT_CERTIFICATE_AND_VERIFY;
      logger->Info("mTLS enabled for gRPC server");
    } else {
      // Standard TLS: no client certificate verification
      ssl_opts.client_certificate_request = GRPC_SSL_DONT_REQUEST_CLIENT_CERTIFICATE;
      logger->Info("TLS enabled for gRPC server");
    }
    
    server_creds = grpc::SslServerCredentials(ssl_opts);
  } else {
    // Fallback to insecure mode for backward compatibility
    server_creds = grpc::InsecureServerCredentials();
    logger->Info("Using insecure gRPC server credentials (no TLS configured)");
  }

  builder.RegisterService(&currencyService);
  builder.RegisterService(&healthService);
  builder.AddListeningPort(address, server_creds);

  // Register rate limiting interceptor
  std::vector<std::unique_ptr<grpc::experimental::ServerInterceptorFactoryInterface>> interceptors;
  interceptors.push_back(std::make_unique<RateLimitInterceptorFactory>());
  builder.experimental().SetInterceptorCreators(std::move(interceptors));

  g_server = std::shared_ptr<Server>(builder.BuildAndStart());
  logger->Info("Currency Server listening on port: " + address);
  
  // Service is now ready
  g_is_ready = true;

  // Register signal handlers for SIGINT and SIGTERM
  RegisterShutdownSignalHandlers();

  g_server->Wait();
  g_is_ready = false;
  g_is_healthy = false;
  if (g_http_server) {
    g_http_server->stop();
    http_thread.join();
  }

  // Wait for all components to shut down
  bool shutdown_clean = WaitForShutdownComplete(g_shutdown_timeout);
  
  logger->Info("Shutdown completed, exiting");
  // Determine exit code
  if (shutdown_clean) {
    exit(0);
  } else {
    if (g_received_signal == SIGINT) {
      exit(130);
    } else if (g_received_signal == SIGTERM) {
      exit(143);
    } else {
      exit(1);
    }
  }
}
}

int main(int argc, char **argv) {

  if (argc < 2) {
    std::cout << "Usage: currency <port>";
    return 0;
  }

  uint16_t port = atoi(argv[1]);

  initTracer();
  initMeter();
  initLogger();
  currency_counter = initIntCounter("demo.exchange.conversions", version);
  g_rate_limited_counter = initIntCounter("currency_service_rate_limited_requests_total", version);
  logger = getLogger(name);

  // Load initial rates configuration
  const char* rates_file = std::getenv("CURRENCY_RATES_FILE");
  if (rates_file != nullptr && strlen(rates_file) > 0) {
    logger->Info("Loading initial rates from file: " + std::string(rates_file));
    load_rates_from_file(rates_file);
  } else {
    // Apply environment overrides to hardcoded defaults
    std::unique_lock<std::shared_mutex> lock(rates_mutex);
    current_rates = apply_env_overrides(current_rates);
    logger->Info("Using hardcoded default rates with environment overrides applied");
  }

  // Start refresh loop if configured
  int refresh_interval = 3600; // default 1 hour
  const char* refresh_interval_env = std::getenv("CURRENCY_RATES_REFRESH_INTERVAL_SECONDS");
  if (refresh_interval_env != nullptr) {
    try {
      refresh_interval = std::stoi(refresh_interval_env);
      if (refresh_interval < 10) {
        logger->Warning("Refresh interval " + std::to_string(refresh_interval) + "s is below minimum, using 10s");
        refresh_interval = 10;
      }
    } catch (const std::exception& e) {
      logger->Error("Invalid refresh interval value, using default 3600s");
    }
  }

  if (rates_file != nullptr && strlen(rates_file) > 0) {
    start_refresh_loop(refresh_interval);
    logger->Info("Automatic rate refresh configured with interval " + std::to_string(refresh_interval) + "s");
  }

  // Parse shutdown timeout configuration
  const char* shutdown_timeout_env = std::getenv("CURRENCY_SERVICE_SHUTDOWN_TIMEOUT_SEC");
  if (shutdown_timeout_env != nullptr && strlen(shutdown_timeout_env) > 0) {
    try {
      int timeout_val = std::stoi(shutdown_timeout_env);
      if (timeout_val <= 0) {
        logger->Warning("Invalid CURRENCY_SERVICE_SHUTDOWN_TIMEOUT_SEC value: " + std::string(shutdown_timeout_env) + ", using default 10s");
      } else {
        g_shutdown_timeout = std::chrono::seconds(timeout_val);
        logger->Info("Using configured shutdown timeout: " + std::to_string(timeout_val) + "s");
      }
    } catch (const std::exception& e) {
      logger->Warning("Invalid CURRENCY_SERVICE_SHUTDOWN_TIMEOUT_SEC value: " + std::string(shutdown_timeout_env) + " is not a valid integer, using default 10s");
    }
  }

  // Parse rate limit configuration
  const char* rate_limit_env = std::getenv("CURRENCY_SERVICE_RATE_LIMIT_RPS");
  if (rate_limit_env != nullptr && strlen(rate_limit_env) > 0) {
    try {
      int rate_limit_val = std::stoi(rate_limit_env);
      if (rate_limit_val > 0) {
        g_rate_limit_rps = rate_limit_val;
        logger->Info("Using configured per-IP rate limit: " + std::to_string(rate_limit_val) + " requests per second");
      } else {
        g_rate_limit_rps = 0;
        logger->Info("Rate limiting disabled (configured value <= 0)");
      }
    } catch (const std::exception& e) {
      logger->Warning("Invalid CURRENCY_SERVICE_RATE_LIMIT_RPS value: " + std::string(rate_limit_env) + " is not a valid integer, rate limiting disabled");
      g_rate_limit_rps = 0;
    }
  } else {
    g_rate_limit_rps = 0;
    logger->Info("Rate limiting disabled (environment variable not set)");
  }

  RunServer(port);

  // Cleanup refresh thread
  refresh_running = false;
  if (refresh_thread.joinable()) {
    refresh_thread.join();
  }

  return 0;
}
