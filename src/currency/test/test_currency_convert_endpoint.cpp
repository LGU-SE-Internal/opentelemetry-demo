#include <gtest/gtest.h>
#include <grpcpp/grpcpp.h>
#include <string>
#include <unordered_map>
#include "currency_service.grpc.pb.h"
#include "currency_service.pb.h"

using grpc::Channel;
using grpc::ClientContext;
using grpc::Status;
using opentelemetry::demo::currency::CurrencyService;
using opentelemetry::demo::currency::ConversionRequest;
using opentelemetry::demo::currency::ConversionResponse;

// Helper to get current rates map from service
extern std::unordered_map<std::string, double> get_current_rates();
// Helper to reset service state between tests
extern void reset_service_state();

class CurrencyConvertEndpointTest : public ::testing::Test {
protected:
    void SetUp() override {
        reset_service_state();
        stub_ = CurrencyService::NewStub(grpc::CreateChannel("localhost:8080", grpc::InsecureChannelCredentials()));
    }

    std::unique_ptr<CurrencyService::Stub> stub_;
};

// AC-1: Invalid from currency code returns INVALID_ARGUMENT with from code in error message
TEST_F(CurrencyConvertEndpointTest, test_ac1_invalid_from_currency_returns_error) {
    ConversionRequest request;
    request.set_units(100);
    request.set_nanos(0);
    request.set_from_currency_code("INVALID");
    request.set_to_currency_code("USD");

    ConversionResponse response;
    ClientContext context;

    Status status = stub_->Convert(&context, request, &response);

    EXPECT_EQ(status.error_code(), grpc::StatusCode::INVALID_ARGUMENT);
    EXPECT_NE(status.error_message().find("unsupported currency code: INVALID"), std::string::npos);
}

// AC-2: Invalid to currency code returns INVALID_ARGUMENT with to code in error message
TEST_F(CurrencyConvertEndpointTest, test_ac2_invalid_to_currency_returns_error) {
    ConversionRequest request;
    request.set_units(100);
    request.set_nanos(0);
    request.set_from_currency_code("USD");
    request.set_to_currency_code("INVALID");

    ConversionResponse response;
    ClientContext context;

    Status status = stub_->Convert(&context, request, &response);

    EXPECT_EQ(status.error_code(), grpc::StatusCode::INVALID_ARGUMENT);
    EXPECT_NE(status.error_message().find("unsupported currency code: INVALID"), std::string::npos);
}

// AC-3: Both from and to invalid returns error with first invalid code
TEST_F(CurrencyConvertEndpointTest, test_ac3_both_invalid_currencies_return_first_error) {
    ConversionRequest request;
    request.set_units(100);
    request.set_nanos(0);
    request.set_from_currency_code("INVALID_FROM");
    request.set_to_currency_code("INVALID_TO");

    ConversionResponse response;
    ClientContext context;

    Status status = stub_->Convert(&context, request, &response);

    EXPECT_EQ(status.error_code(), grpc::StatusCode::INVALID_ARGUMENT);
    EXPECT_NE(status.error_message().find("unsupported currency code: INVALID_FROM"), std::string::npos);
    // Should mention first invalid code, not the second one
    EXPECT_EQ(status.error_message().find("INVALID_TO"), std::string::npos);
}

// AC-4: Invalid currency calls do not pollute the rates map
TEST_F(CurrencyConvertEndpointTest, test_ac4_invalid_currency_no_map_pollution) {
    // First get initial rates keys
    auto initial_rates = get_current_rates();
    std::vector<std::string> initial_keys;
    for (const auto& [code, _] : initial_rates) {
        initial_keys.push_back(code);
    }

    // Make multiple invalid calls
    std::vector<std::string> invalid_codes = {"XXX", "YYY", "ZZZ", "FAKE", "TEST"};
    for (const auto& code : invalid_codes) {
        ConversionRequest request;
        request.set_units(100);
        request.set_nanos(0);
        request.set_from_currency_code(code);
        request.set_to_currency_code("USD");

        ConversionResponse response;
        ClientContext context;
        stub_->Convert(&context, request, &response);
    }

    // Check rates map has no invalid codes
    auto rates_after = get_current_rates();
    for (const auto& code : invalid_codes) {
        EXPECT_EQ(rates_after.count(code), 0) << "Invalid code " << code << " found in rates map";
    }

    // Check all initial keys are still present
    for (const auto& code : initial_keys) {
        EXPECT_EQ(rates_after.count(code), 1) << "Initial code " << code << " missing from rates map";
    }
}

// AC-5: Valid currency conversions still work correctly
TEST_F(CurrencyConvertEndpointTest, test_ac5_valid_conversions_still_work) {
    ConversionRequest request;
    request.set_units(100);
    request.set_nanos(0);
    request.set_from_currency_code("USD");
    request.set_to_currency_code("EUR");

    ConversionResponse response;
    ClientContext context;

    Status status = stub_->Convert(&context, request, &response);

    EXPECT_TRUE(status.ok());
    EXPECT_GT(response.units(), 0);
    // Verify result is roughly 90 EUR for 100 USD (based on default rate 0.9)
    EXPECT_NEAR(response.units() + response.nanos() / 1e9, 90.0, 0.01);
}

// AC-6: No division by zero or crashes when using invalid currency codes
TEST_F(CurrencyConvertEndpointTest, test_ac6_no_crashes_or_division_by_zero) {
    // Test multiple invalid cases, should not crash or throw
    EXPECT_NO_THROW({
        std::vector<std::pair<std::string, std::string>> test_cases = {
            {"INVALID", "USD"},
            {"USD", "INVALID"},
            {"INVALID1", "INVALID2"},
            {"", "USD"},
            {"USD", ""},
            {"123", "456"},
            {"!!!", "$$$"}
        };

        for (const auto& [from, to] : test_cases) {
            ConversionRequest request;
            request.set_units(100);
            request.set_nanos(0);
            request.set_from_currency_code(from);
            request.set_to_currency_code(to);

            ConversionResponse response;
            ClientContext context;
            Status status = stub_->Convert(&context, request, &response);
            // Just need to ensure no crash, error status is expected
        }
    });
}

// AC-7: Existing valid conversion tests still pass
TEST_F(CurrencyConvertEndpointTest, test_ac7_existing_valid_tests_pass) {
    // Test all valid pairs from default rates: USD, EUR, JPY, GBP, CAD
    std::vector<std::pair<std::string, std::string>> valid_pairs = {
        {"USD", "EUR"}, {"EUR", "USD"}, {"USD", "JPY"}, {"JPY", "USD"},
        {"GBP", "CAD"}, {"CAD", "GBP"}, {"EUR", "JPY"}, {"JPY", "EUR"}
    };

    for (const auto& [from, to] : valid_pairs) {
        ConversionRequest request;
        request.set_units(100);
        request.set_nanos(0);
        request.set_from_currency_code(from);
        request.set_to_currency_code(to);

        ConversionResponse response;
        ClientContext context;
        Status status = stub_->Convert(&context, request, &response);
        EXPECT_TRUE(status.ok()) << "Conversion from " << from << " to " << to << " failed";
    }
}

int main(int argc, char **argv) {
    testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}
