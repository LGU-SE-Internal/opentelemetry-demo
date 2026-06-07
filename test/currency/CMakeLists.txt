cmake_minimum_required(VERSION 3.18)
project(currency_rate_limit_test)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED True)

find_package(gRPC CONFIG REQUIRED)
find_package(Protobuf CONFIG REQUIRED)
find_package(GTest REQUIRED)
find_package(opentelemetry-cpp CONFIG REQUIRED)

add_executable(test_rate_limiting test_rate_limiting.cc)

target_link_libraries(test_rate_limiting
  PRIVATE
    gtest::gtest
    gtest::gtest_main
    gRPC::grpc++
    protobuf::libprotobuf
    opentelemetry-cpp::api
    opentelemetry-cpp::sdk
    opentelemetry-cpp::metrics
    currency_service_proto
)

include(GoogleTest)
gtest_discover_tests(test_rate_limiting)
