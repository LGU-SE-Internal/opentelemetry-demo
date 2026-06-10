#include <gtest/gtest.h>
#include <grpcpp/grpcpp.h>
#include "demo.grpc.pb.h"
#include "../src/currency_service.h"

using namespace oteldemo;
using grpc::Status;
using grpc::StatusCode;

class CurrencyServiceTest : public ::testing::Test {
protected:
    CurrencyServiceImpl service;
};

TEST_F(CurrencyServiceTest, test_ac1_from_currency_empty) {
    ConvertRequest request;
    ConvertResponse response;
    grpc::ServerContext context;

    request.set_source_currency("");
    request.set_target_currency("USD");
    request.mutable_amount()->set_units(100);
    request.mutable_amount()->set_nanos(0);

    Status status = service.Convert(&context, &request, &response);

    EXPECT_EQ(status.error_code(), StatusCode::INVALID_ARGUMENT);
    EXPECT_TRUE(status.error_message().find("from_currency cannot be empty") != std::string::npos);
}

TEST_F(CurrencyServiceTest, test_ac2_to_currency_empty) {
    ConvertRequest request;
    ConvertResponse response;
    grpc::ServerContext context;

    request.set_source_currency("USD");
    request.set_target_currency("");
    request.mutable_amount()->set_units(100);
    request.mutable_amount()->set_nanos(0);

    Status status = service.Convert(&context, &request, &response);

    EXPECT_EQ(status.error_code(), StatusCode::INVALID_ARGUMENT);
    EXPECT_TRUE(status.error_message().find("to_currency cannot be empty") != std::string::npos);
}

TEST_F(CurrencyServiceTest, test_ac3_from_currency_not_supported) {
    ConvertRequest request;
    ConvertResponse response;
    grpc::ServerContext context;

    const std::string invalid_code = "INVALID";
    request.set_source_currency(invalid_code);
    request.set_target_currency("USD");
    request.mutable_amount()->set_units(100);
    request.mutable_amount()->set_nanos(0);

    Status status = service.Convert(&context, &request, &response);

    EXPECT_EQ(status.error_code(), StatusCode::INVALID_ARGUMENT);
    std::string expected_substring = "from_currency " + invalid_code + " is not supported";
    EXPECT_TRUE(status.error_message().find(expected_substring) != std::string::npos);
}

TEST_F(CurrencyServiceTest, test_ac4_to_currency_not_supported) {
    ConvertRequest request;
    ConvertResponse response;
    grpc::ServerContext context;

    const std::string invalid_code = "INVALID";
    request.set_source_currency("USD");
    request.set_target_currency(invalid_code);
    request.mutable_amount()->set_units(100);
    request.mutable_amount()->set_nanos(0);

    Status status = service.Convert(&context, &request, &response);

    EXPECT_EQ(status.error_code(), StatusCode::INVALID_ARGUMENT);
    std::string expected_substring = "to_currency " + invalid_code + " is not supported";
    EXPECT_TRUE(status.error_message().find(expected_substring) != std::string::npos);
}

TEST_F(CurrencyServiceTest, test_ac5_amount_negative_units) {
    ConvertRequest request;
    ConvertResponse response;
    grpc::ServerContext context;

    request.set_source_currency("USD");
    request.set_target_currency("EUR");
    request.mutable_amount()->set_units(-100);
    request.mutable_amount()->set_nanos(0);

    Status status = service.Convert(&context, &request, &response);

    EXPECT_EQ(status.error_code(), StatusCode::INVALID_ARGUMENT);
    EXPECT_TRUE(status.error_message().find("amount cannot be negative") != std::string::npos);
}

TEST_F(CurrencyServiceTest, test_ac5_amount_negative_nanos) {
    ConvertRequest request;
    ConvertResponse response;
    grpc::ServerContext context;

    request.set_source_currency("USD");
    request.set_target_currency("EUR");
    request.mutable_amount()->set_units(100);
    request.mutable_amount()->set_nanos(-500000000);

    Status status = service.Convert(&context, &request, &response);

    EXPECT_EQ(status.error_code(), StatusCode::INVALID_ARGUMENT);
    EXPECT_TRUE(status.error_message().find("amount cannot be negative") != std::string::npos);
}

TEST_F(CurrencyServiceTest, test_ac6_valid_request_no_validation_error) {
    ConvertRequest request;
    ConvertResponse response;
    grpc::ServerContext context;

    request.set_source_currency("USD");
    request.set_target_currency("EUR");
    request.mutable_amount()->set_units(100);
    request.mutable_amount()->set_nanos(500000000);

    Status status = service.Convert(&context, &request, &response);

    EXPECT_TRUE(status.ok());
}

int main(int argc, char **argv) {
    ::testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}
