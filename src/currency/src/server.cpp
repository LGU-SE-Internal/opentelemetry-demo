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
  std::unordered_map<std::string, double> currency_conversion
  {
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

  std::string version = std::getenv("VERSION"); 
  std::string name{ "currency" };

  nostd::unique_ptr<metrics_api::Counter<uint64_t>> currency_counter;
  nostd::shared_ptr<opentelemetry::logs::Logger> logger;

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

    for (auto &code : currency_conversion) {
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
      
      // Validate from currency code exists in supported list
      if (currency_conversion.find(from_code) == currency_conversion.end()) {
        span->SetStatus(StatusCode::kError);
        logger->Error(std::string(__func__) + " conversion failed: source currency code '" + from_code + "' is not supported");
        span->End();
        return Status(grpc::INVALID_ARGUMENT, "Source currency code '" + from_code + "' is not supported");
      }
      
      // Validate amount is finite and non-negative
      double amount = getDouble(from);
      if (std::isnan(amount) || std::isinf(amount)) {
        span->SetStatus(StatusCode::kError);
        logger->Error(std::string(__func__) + " conversion failed: amount is not a valid monetary value");
        span->End();
        return Status(grpc::INVALID_ARGUMENT, "Amount is not a valid monetary value: " + std::to_string(amount));
      }
      
      if (amount < 0.0) {
        span->SetStatus(StatusCode::kError);
        logger->Error(std::string(__func__) + " conversion failed: amount cannot be negative: " + std::to_string(amount));
        span->End();
        return Status(grpc::INVALID_ARGUMENT, "Amount cannot be negative: " + std::to_string(amount));
      }
      
      double rate = currency_conversion[from_code];
      double one_euro = amount / rate ;

      string to_code = request->to_code();
      
      // Validate to currency code exists in supported list
      if (currency_conversion.find(to_code) == currency_conversion.end()) {
        span->SetStatus(StatusCode::kError);
        logger->Error(std::string(__func__) + " conversion failed: target currency code '" + to_code + "' is not supported");
        span->End();
        return Status(grpc::INVALID_ARGUMENT, "Target currency code '" + to_code + "' is not supported");
      }
      
      double to_rate = currency_conversion[to_code];

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
std::unique_ptr<Server> g_server;
std::unique_ptr<httplib::Server> g_http_server;
std::atomic<bool> g_is_healthy{true};
std::atomic<bool> g_is_ready{false};

// Signal handler to trigger graceful shutdown
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
  const char* tls_cert_path = std::getenv("CURRENCY_SERVICE_TLS_CERT_PATH");
  const char* tls_key_path = std::getenv("CURRENCY_SERVICE_TLS_KEY_PATH");
  const char* tls_ca_path = std::getenv("CURRENCY_SERVICE_TLS_CA_CERT_PATH");
  
  std::shared_ptr<grpc::ServerCredentials> server_creds;
  
  bool tls_enabled = (tls_cert_path != nullptr) || (tls_key_path != nullptr) || (tls_ca_path != nullptr);
  
  if (tls_enabled) {
    // Validate configuration
    if (tls_ca_path != nullptr && (tls_cert_path == nullptr || tls_key_path == nullptr)) {
      logger->Error("mTLS configuration requires server TLS certificate and key to be provided");
      exit(EINVAL);
    }
    
    if ((tls_cert_path != nullptr && tls_key_path == nullptr) || (tls_cert_path == nullptr && tls_key_path != nullptr)) {
      logger->Error("Both TLS certificate path and private key path must be provided to enable TLS");
      exit(EINVAL);
    }
    
    // Check all files exist
    if (!file_exists(tls_cert_path)) {
      logger->Error("TLS file " + std::string(tls_cert_path) + " is missing or unreadable");
      exit(ENOENT);
    }
    if (!file_exists(tls_key_path)) {
      logger->Error("TLS file " + std::string(tls_key_path) + " is missing or unreadable");
      exit(ENOENT);
    }
    if (tls_ca_path != nullptr && !file_exists(tls_ca_path)) {
      logger->Error("TLS file " + std::string(tls_ca_path) + " is missing or unreadable");
      exit(ENOENT);
    }
    
    // Read certificate files
    std::string cert_data = read_file(tls_cert_path);
    std::string key_data = read_file(tls_key_path);
    
    if (cert_data.empty() || key_data.empty()) {
      logger->Error("Invalid TLS certificate or private key");
      exit(EINVAL);
    }
    
    grpc::SslServerCredentialsOptions ssl_opts;
    grpc::SslServerCredentialsOptions::PemKeyCertPair key_cert_pair;
    key_cert_pair.private_key = key_data;
    key_cert_pair.cert_chain = cert_data;
    ssl_opts.pem_key_cert_pairs.push_back(key_cert_pair);
    
    if (tls_ca_path != nullptr) {
      // mTLS mode: require client certificate verification
      std::string ca_data = read_file(tls_ca_path);
      if (ca_data.empty()) {
        logger->Error("Invalid CA certificate");
        exit(EINVAL);
      }
      ssl_opts.pem_root_certs = ca_data;
      ssl_opts.client_certificate_request = GRPC_SSL_REQUEST_AND_REQUIRE_CLIENT_CERTIFICATE_AND_VERIFY;
    } else {
      // Standard TLS: no client certificate verification
      ssl_opts.client_certificate_request = GRPC_SSL_DONT_REQUEST_CLIENT_CERTIFICATE;
    }
    
    server_creds = grpc::SslServerCredentials(ssl_opts);
    logger->Info("TLS enabled for gRPC server" + std::string(tls_ca_path != nullptr ? " with mTLS" : ""));
  } else {
    // Fallback to insecure mode for backward compatibility
    server_creds = grpc::InsecureServerCredentials();
    logger->Info("Using insecure gRPC server credentials (no TLS configured)");
  }

  builder.RegisterService(&currencyService);
  builder.RegisterService(&healthService);
  builder.AddListeningPort(address, server_creds);

  g_server = std::unique_ptr<Server>(builder.BuildAndStart());
  logger->Info("Currency Server listening on port: " + address);
  
  // Service is now ready
  g_is_ready = true;

  // Register signal handlers for SIGINT and SIGTERM
  std::signal(SIGINT, SignalHandler);
  std::signal(SIGTERM, SignalHandler);

  g_server->Wait();
  g_is_ready = false;
  g_is_healthy = false;
  g_http_server->stop();
  http_thread.join();
  g_server->Shutdown();
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
  logger = getLogger(name);
  RunServer(port);

  return 0;
}
