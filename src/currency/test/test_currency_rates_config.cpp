#include <gtest/gtest.h>
#include <unordered_map>
#include <string>
#include <fstream>
#include <cstdlib>
#include <chrono>
#include <thread>
#include <filesystem>

// Import the interfaces from the spec directly
extern std::unordered_map<std::string, double> get_current_rates();
extern bool load_rates_from_file(const std::string& file_path);
extern std::unordered_map<std::string, double> apply_env_overrides(std::unordered_map<std::string, double> base_rates);
extern void start_refresh_loop(int interval_seconds);

namespace fs = std::filesystem;

// Hardcoded original rates reference (from existing service)
const std::unordered_map<std::string, double> HARDCODED_RATES = {
    {"USD", 1.0},
    {"EUR", 0.9},
    {"JPY", 140.0},
    {"GBP", 0.8},
    {"CAD", 1.35}
};

// Helper to create temp JSON rate file
std::string create_test_rate_file(const std::unordered_map<std::string, double>& rates) {
    auto temp_path = fs::temp_directory_path() / "test_rates.json";
    std::ofstream out(temp_path);
    out << "{\n  \"rates\": {\n";
    bool first = true;
    for (const auto& [code, rate] : rates) {
        if (!first) out << ",\n";
        first = false;
        out << "    \"" << code << "\": " << rate;
    }
    out << "\n  }\n}\n";
    out.close();
    return temp_path.string();
}

// Helper to clear all currency env vars
void clear_currency_env_vars() {
    unsetenv("CURRENCY_RATES_FILE");
    unsetenv("CURRENCY_RATES_REFRESH_INTERVAL_SECONDS");
    // Clear all CURRENCY_RATE_* vars
    extern char** environ;
    std::vector<std::string> vars_to_remove;
    for (char** env = environ; *env != nullptr; ++env) {
        std::string env_str(*env);
        if (env_str.starts_with("CURRENCY_RATE_")) {
            size_t eq_pos = env_str.find('=');
            if (eq_pos != std::string::npos) {
                vars_to_remove.push_back(env_str.substr(0, eq_pos));
            }
        }
    }
    for (const auto& var : vars_to_remove) {
        unsetenv(var.c_str());
    }
}

// AC-1: No config provided → return hardcoded rates
TEST(CurrencyRatesTest, test_ac1_hardcoded_rates_when_no_config) {
    clear_currency_env_vars();
    auto rates = get_current_rates();
    EXPECT_EQ(rates, HARDCODED_RATES);
}

// AC-2: Valid config file provided → use rates from file
TEST(CurrencyRatesTest, test_ac2_load_rates_from_valid_file) {
    clear_currency_env_vars();
    std::unordered_map<std::string, double> test_rates = {
        {"USD", 1.0},
        {"EUR", 0.92},
        {"JPY", 145.2},
        {"GBP", 0.79}
    };
    auto file_path = create_test_rate_file(test_rates);
    setenv("CURRENCY_RATES_FILE", file_path.c_str(), 1);
    
    // Service would load on startup, but we call explicitly for test
    EXPECT_TRUE(load_rates_from_file(file_path));
    auto rates = get_current_rates();
    EXPECT_EQ(rates, test_rates);
    
    fs::remove(file_path);
    unsetenv("CURRENCY_RATES_FILE");
}

// AC-3: Env overrides replace base rates
TEST(CurrencyRatesTest, test_ac3_env_overrides_apply_over_base) {
    clear_currency_env_vars();
    // Test override over hardcoded
    setenv("CURRENCY_RATE_EUR", "0.95", 1);
    setenv("CURRENCY_RATE_AUD", "1.5", 1);
    auto rates = apply_env_overrides(HARDCODED_RATES);
    EXPECT_EQ(rates.at("EUR"), 0.95);
    EXPECT_EQ(rates.at("AUD"), 1.5);
    EXPECT_EQ(rates.at("USD"), HARDCODED_RATES.at("USD")); // unchanged
    
    // Test override over file rates
    std::unordered_map<std::string, double> file_rates = {{"EUR", 0.92}, {"USD", 1.0}};
    setenv("CURRENCY_RATE_EUR", "0.98", 1);
    rates = apply_env_overrides(file_rates);
    EXPECT_EQ(rates.at("EUR"), 0.98);
    unsetenv("CURRENCY_RATE_EUR");
    unsetenv("CURRENCY_RATE_AUD");
}

// AC-4: Refresh interval reloads file automatically
TEST(CurrencyRatesTest, test_ac4_automatic_refresh_at_interval) {
    clear_currency_env_vars();
    std::unordered_map<std::string, double> initial_rates = {{"USD", 1.0}, {"EUR", 0.92}};
    auto file_path = create_test_rate_file(initial_rates);
    setenv("CURRENCY_RATES_FILE", file_path.c_str(), 1);
    
    load_rates_from_file(file_path);
    auto rates_before = get_current_rates();
    EXPECT_EQ(rates_before.at("EUR"), 0.92);
    
    // Update file with new rate
    std::unordered_map<std::string, double> new_rates = {{"USD", 1.0}, {"EUR", 0.96}};
    std::ofstream out(file_path);
    out << "{\n  \"rates\": {\n    \"USD\": 1.0,\n    \"EUR\": 0.96\n  }\n}\n";
    out.close();
    
    // Start refresh loop with 10s minimum (use 10s for test)
    start_refresh_loop(10);
    // Wait 11 seconds to allow refresh
    std::this_thread::sleep_for(std::chrono::seconds(11));
    
    auto rates_after = get_current_rates();
    EXPECT_EQ(rates_after.at("EUR"), 0.96);
    
    fs::remove(file_path);
    unsetenv("CURRENCY_RATES_FILE");
}

// AC-5: Invalid file → fall back to last valid rates
TEST(CurrencyRatesTest, test_ac5_invalid_file_fallback_to_last_valid) {
    clear_currency_env_vars();
    std::unordered_map<std::string, double> valid_rates = {{"USD", 1.0}, {"EUR", 0.92}};
    auto file_path = create_test_rate_file(valid_rates);
    setenv("CURRENCY_RATES_FILE", file_path.c_str(), 1);
    
    EXPECT_TRUE(load_rates_from_file(file_path));
    auto valid = get_current_rates();
    EXPECT_EQ(valid.at("EUR"), 0.92);
    
    // Write invalid JSON to file
    std::ofstream out(file_path);
    out << "invalid json { bad }";
    out.close();
    
    EXPECT_FALSE(load_rates_from_file(file_path));
    auto rates_after_fail = get_current_rates();
    EXPECT_EQ(rates_after_fail, valid); // same as last valid
    
    // Delete file, load should fail, still keep last valid
    fs::remove(file_path);
    EXPECT_FALSE(load_rates_from_file(file_path));
    rates_after_fail = get_current_rates();
    EXPECT_EQ(rates_after_fail, valid);
}

// AC-6: Non-numeric rate skipped, keep existing
TEST(CurrencyRatesTest, test_ac6_non_numeric_rate_skipped) {
    clear_currency_env_vars();
    // Test invalid env var
    setenv("CURRENCY_RATE_EUR", "not_a_number", 1);
    auto rates = apply_env_overrides(HARDCODED_RATES);
    EXPECT_EQ(rates.at("EUR"), HARDCODED_RATES.at("EUR")); // unchanged
    
    // Test invalid rate in file
    auto file_path = fs::temp_directory_path() / "test_invalid_rate.json";
    std::ofstream out(file_path);
    out << R"({
  "rates": {
    "USD": 1.0,
    "EUR": "invalid",
    "JPY": 145
  }
})";
    out.close();
    EXPECT_TRUE(load_rates_from_file(file_path));
    rates = get_current_rates();
    EXPECT_EQ(rates.at("USD"), 1.0);
    EXPECT_EQ(rates.at("JPY"), 145);
    EXPECT_EQ(rates.at("EUR"), HARDCODED_RATES.at("EUR")); // existing kept
    
    fs::remove(file_path);
    unsetenv("CURRENCY_RATE_EUR");
}

// AC-7: Refresh interval <10 → use 10s minimum
TEST(CurrencyRatesTest, test_ac7_refresh_interval_minimum_10s) {
    clear_currency_env_vars();
    // Start with 5s interval, should use 10s
    std::unordered_map<std::string, double> initial_rates = {{"USD", 1.0}, {"EUR", 0.92}};
    auto file_path = create_test_rate_file(initial_rates);
    setenv("CURRENCY_RATES_FILE", file_path.c_str(), 1);
    
    load_rates_from_file(file_path);
    start_refresh_loop(5); // below minimum
    
    // Update file after 6 seconds, check not refreshed yet
    std::this_thread::sleep_for(std::chrono::seconds(6));
    std::ofstream out(file_path);
    out << "{\n  \"rates\": {\n    \"USD\": 1.0,\n    \"EUR\": 0.96\n  }\n}\n";
    out.close();
    
    std::this_thread::sleep_for(std::chrono::seconds(3)); // total 9s, still no refresh
    auto rates_before = get_current_rates();
    EXPECT_EQ(rates_before.at("EUR"), 0.92);
    
    std::this_thread::sleep_for(std::chrono::seconds(2)); // total 11s, should refresh
    auto rates_after = get_current_rates();
    EXPECT_EQ(rates_after.at("EUR"), 0.96);
    
    fs::remove(file_path);
}

// AC-9: Existing API returns same schema as before
// (Assuming existing Convert method exists, test it returns same structure)
extern std::string convert_currency(double amount, const std::string& from_code, const std::string& to_code);
TEST(CurrencyRatesTest, test_ac9_existing_api_backwards_compatible) {
    clear_currency_env_vars();
    // Test existing conversion returns valid numeric value, no errors
    auto result = convert_currency(100.0, "USD", "EUR");
    // Ensure result is a valid number string, no breaking changes
    EXPECT_NO_THROW(std::stod(result));
    EXPECT_GT(std::stod(result), 0.0);
}

int main(int argc, char **argv) {
    testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}
