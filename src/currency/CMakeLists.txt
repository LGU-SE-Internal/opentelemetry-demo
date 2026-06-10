# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

cmake_minimum_required(VERSION 3.26)
project(currency)

find_package(Protobuf REQUIRED)
find_package(gRPC CONFIG REQUIRED)
find_package(opentelemetry-cpp CONFIG REQUIRED)
find_package(nlohmann_json REQUIRED)
find_package(GTest REQUIRED)

set(GENERATED_PROTOBUF_PATH "${CMAKE_BINARY_DIR}/generated/proto")

add_subdirectory(genproto)

include_directories("${GENERATED_PROTOBUF_PATH}" "${OPENTELEMETRY_CPP_INCLUDE_DIRS}")

add_executable(currency src/server.cpp)
add_dependencies(currency demo-proto)
target_link_libraries(
    currency demo-proto protobuf::libprotobuf
     ${OPENTELEMETRY_CPP_LIBRARIES} gRPC::grpc++ nlohmann_json::nlohmann_json)

install(TARGETS currency DESTINATION bin)

# Test executables
add_executable(test_currency_rates_config test/test_currency_rates_config.cpp src/server.cpp)
add_dependencies(test_currency_rates_config demo-proto)
target_link_libraries(
    test_currency_rates_config demo-proto protobuf::libprotobuf
    ${OPENTELEMETRY_CPP_LIBRARIES} gRPC::grpc++ nlohmann_json::nlohmann_json GTest::GTest GTest::Main)

add_executable(test_currency_graceful_shutdown test/test_currency_graceful_shutdown.cpp src/server.cpp)
add_dependencies(test_currency_graceful_shutdown demo-proto)
target_link_libraries(
    test_currency_graceful_shutdown demo-proto protobuf::libprotobuf
    ${OPENTELEMETRY_CPP_LIBRARIES} gRPC::grpc++ nlohmann_json::nlohmann_json GTest::GTest GTest::Main)

add_executable(convert_validation_test test/convert_validation_test.cpp src/server.cpp)
add_dependencies(convert_validation_test demo-proto)
target_link_libraries(
    convert_validation_test demo-proto protobuf::libprotobuf
    ${OPENTELEMETRY_CPP_LIBRARIES} gRPC::grpc++ nlohmann_json::nlohmann_json GTest::GTest GTest::Main)
