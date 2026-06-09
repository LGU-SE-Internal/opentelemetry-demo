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

// AC-3: Negative units value returns INVALID_ARGUMENT with descriptive error
TEST_F(CurrencyConvertEndpointTest, test_ac3_negative_units_returns_error) {
    ConversionRequest request;
    request.set_units(-100);
    request.set_nanos(0);
    request.set_from_currency_code("USD");
    request.set_to_currency_code("EUR");

    ConversionResponse response;
    ClientContext context;

    Status status = stub_->Convert(&context, request, &response);

    EXPECT_EQ(status.error_code(), grpc::StatusCode::INVALID_ARGUMENT);
    EXPECT_NE(status.error_message().find("Monetary amount units cannot be negative: -100"), std::string::npos);
}

// AC-4: Negative nanos value returns INVALID_ARGUMENT with descriptive error
TEST_F(CurrencyConvertEndpointTest, test_ac4_negative_nanos_returns_error) {
    ConversionRequest request;
    request.set_units(100);
    request.set_nanos(-1);
    request.set_from_currency_code("USD");
    request.set_to_currency_code("EUR");

    ConversionResponse response;
    ClientContext context;

    Status status = stub_->Convert(&context, request, &response);

    EXPECT_EQ(status.error_code(), grpc::StatusCode::INVALID_ARGUMENT);
    EXPECT_NE(status.error_message().find("Monetary amount nanos must be between 0 and 999,999,999: -1"), std::string::npos);
}

// AC-5: Nanos value exceeding 999,999,999 returns INVALID_ARGUMENT with descriptive error
TEST_F(CurrencyConvertEndpointTest, test_ac5_nanos_exceeding_max_returns_error) {
    ConversionRequest request;
    request.set_units(100);
    request.set_nanos(1000000000);
    request.set_from_currency_code("USD");
    request.set_to_currency_code("EUR");

    ConversionResponse response;
    ClientContext context;

    Status status = stub_->Convert(&context, request, &response);

    EXPECT_EQ(status.error_code(), grpc::StatusCode::INVALID_ARGUMENT);
    EXPECT_NE(status.error_message().find("Monetary amount nanos must be between 0 and 999,999,999: 1000000000"), std::string::npos);
}

// AC-6: All valid fields work correctly, conversion proceeds normally
TEST_F(CurrencyConvertEndpointTest, test_ac6_all_valid_fields_convert_successfully) {
    ConversionRequest request;
    request.set_units(123);
    request.set_nanos(456789012);
    request.set_from_currency_code("USD");
    request.set_to_currency_code("CAD");

    ConversionResponse response;
    ClientContext context;

    Status status = stub_->Convert(&context, request, &response);

    EXPECT_TRUE(status.ok());
    EXPECT_GE(response.units(), 0);
    EXPECT_GE(response.nanos(), 0);
    EXPECT_LE(response.nanos(), 999999999);
}

// AC-7: All validation failure cases return correct error code and message
TEST_F(CurrencyConvertEndpointTest, test_ac7_all_validation_cases_covered) {
    std::vector<std::tuple<ConversionRequest, std::string>> test_cases = {
        // Invalid source currency
        [](){
            ConversionRequest r;
            r.set_units(100); r.set_nanos(0); r.set_from_currency_code("XXX"); r.set_to_currency_code("USD");
            return r;
        }(), "Invalid source currency code: XXX",
        // Invalid target currency
        [](){
            ConversionRequest r;
            r.set_units(100); r.set_nanos(0); r.set_from_currency_code("USD"); r.set_to_currency_code("XXX");
            return r;
        }(), "Invalid target currency code: XXX",
        // Negative units
        [](){
            ConversionRequest r;
            r.set_units(-50); r.set_nanos(0); r.set_from_currency_code("USD"); r.set_to_currency_code("EUR");
            return r;
        }(), "Monetary amount units cannot be negative: -50",
        // Negative nanos
        [](){
            ConversionRequest r;
            r.set_units(100); r.set_nanos(-123); r.set_from_currency_code("USD"); r.set_to_currency_code("EUR");
            return r;
        }(), "Monetary amount nanos must be between 0 and 999,999,999: -123",
        // Nanos exceed max
        [](){
            ConversionRequest r;
            r.set_units(100); r.set_nanos(1000000000); r.set_from_currency_code("USD"); r.set_to_currency_code("EUR");
            return r;
        }(), "Monetary amount nanos must be between 0 and 999,999,999: 1000000000"
    };

    for (const auto& [req, expected_msg_substring] : test_cases) {
        ConversionResponse response;
        ClientContext context;
        Status status = stub_->Convert(&context, req, &response);
        EXPECT_EQ(status.error_code(), grpc::StatusCode::INVALID_ARGUMENT);
        EXPECT_NE(status.error_message().find(expected_msg_substring), std::string::npos);
    }
}

// AC-8: No division by zero or crashes when using invalid currency codes
TEST_F(CurrencyConvertEndpointTest, test_ac8_no_crashes_or_division_by_zero) {
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

// AC-9: Existing valid conversion tests still pass
TEST_F(CurrencyConvertEndpointTest, test_ac9_existing_valid_tests_pass) {
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
