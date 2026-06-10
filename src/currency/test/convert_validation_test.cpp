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

TEST_F(CurrencyServiceTest, test_ac1_invalid_source_currency) {
    ConvertRequest request;
    ConvertResponse response;
    grpc::ServerContext context;

    request.set_source_currency("INVALID");
    request.set_target_currency("USD");
    request.mutable_amount()->set_units(100);
    request.mutable_amount()->set_nanos(0);

    Status status = service.Convert(&context, &request, &response);

    EXPECT_EQ(status.error_code(), StatusCode::INVALID_ARGUMENT);
    EXPECT_EQ(status.error_message(), "Source currency code INVALID is not supported");
}

TEST_F(CurrencyServiceTest, test_ac2_invalid_target_currency) {
    ConvertRequest request;
    ConvertResponse response;
    grpc::ServerContext context;

    request.set_source_currency("USD");
    request.set_target_currency("INVALID");
    request.mutable_amount()->set_units(100);
    request.mutable_amount()->set_nanos(0);

    Status status = service.Convert(&context, &request, &response);

    EXPECT_EQ(status.error_code(), StatusCode::INVALID_ARGUMENT);
    EXPECT_EQ(status.error_message(), "Target currency code INVALID is not supported");
}

TEST_F(CurrencyServiceTest, test_ac3_negative_amount_units) {
    ConvertRequest request;
    ConvertResponse response;
    grpc::ServerContext context;

    request.set_source_currency("USD");
    request.set_target_currency("EUR");
    request.mutable_amount()->set_units(-100);
    request.mutable_amount()->set_nanos(0);

    Status status = service.Convert(&context, &request, &response);

    EXPECT_EQ(status.error_code(), StatusCode::INVALID_ARGUMENT);
    EXPECT_EQ(status.error_message(), "Amount units cannot be negative");
}

TEST_F(CurrencyServiceTest, test_ac4_negative_amount_nanos) {
    ConvertRequest request;
    ConvertResponse response;
    grpc::ServerContext context;

    request.set_source_currency("USD");
    request.set_target_currency("EUR");
    request.mutable_amount()->set_units(100);
    request.mutable_amount()->set_nanos(-500000000);

    Status status = service.Convert(&context, &request, &response);

    EXPECT_EQ(status.error_code(), StatusCode::INVALID_ARGUMENT);
    EXPECT_EQ(status.error_message(), "Amount nanos cannot be negative");
}

TEST_F(CurrencyServiceTest, test_ac5_valid_request_no_validation_error) {
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
