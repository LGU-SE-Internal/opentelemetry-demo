<?php

use PHPUnit\Framework\TestCase;
use InvalidConfigurationException;

class ShippingConfigurationACTest extends TestCase
{
    protected function setUp(): void
    {
        // Reset environment variables before each test
        putenv('SHIPPING_BASE_COST_PER_ITEM');
        putenv('SHIPPING_WEIGHT_SURCHARGE_PER_KG');
        putenv('SHIPPING_MINIMUM_ORDER_COST');
    }

    /**
     * AC-1: When no environment variables are set, calling calculateShippingCost(N, 0) returns exactly N * 8.99 for any positive integer N
     */
    public function test_ac1_default_shipping_cost_without_env_vars()
    {
        $quoteService = new QuoteService();
        
        $this->assertEquals(8.99, $quoteService->calculateShippingCost(1, 0));
        $this->assertEquals(17.98, $quoteService->calculateShippingCost(2, 0));
        $this->assertEquals(44.95, $quoteService->calculateShippingCost(5, 0));
        $this->assertEquals(0.0, $quoteService->calculateShippingCost(0, 0));
    }

    /**
     * AC-2: When SHIPPING_BASE_COST_PER_ITEM=5.99 is set, calling calculateShippingCost(3, 0) returns 17.97
     */
    public function test_ac2_custom_base_cost_per_item()
    {
        putenv('SHIPPING_BASE_COST_PER_ITEM=5.99');
        $quoteService = new QuoteService();
        
        $this->assertEquals(17.97, $quoteService->calculateShippingCost(3, 0));
    }

    /**
     * AC-3: When SHIPPING_WEIGHT_SURCHARGE_PER_KG=2.5 is set, calling calculateShippingCost(1, 4) returns 18.99 (8.99 + (4 * 2.5))
     */
    public function test_ac3_weight_surcharge_applied_correctly()
    {
        putenv('SHIPPING_WEIGHT_SURCHARGE_PER_KG=2.5');
        $quoteService = new QuoteService();
        
        $this->assertEquals(18.99, $quoteService->calculateShippingCost(1, 4));
    }

    /**
     * AC-4: When SHIPPING_MINIMUM_ORDER_COST=15.0 is set, calling calculateShippingCost(1, 0) returns 15.0 (max of 8.99 and 15.0)
     */
    public function test_ac4_minimum_order_cost_enforced()
    {
        putenv('SHIPPING_MINIMUM_ORDER_COST=15.0');
        $quoteService = new QuoteService();
        
        $this->assertEquals(15.0, $quoteService->calculateShippingCost(1, 0));
    }

    /**
     * AC-5: When SHIPPING_BASE_COST_PER_ITEM=-2.0 is set, service fails to start with error message indicating negative value for SHIPPING_BASE_COST_PER_ITEM
     */
    public function test_ac5_negative_base_cost_throws_exception_on_initialization()
    {
        $this->expectException(InvalidConfigurationException::class);
        $this->expectExceptionMessage('SHIPPING_BASE_COST_PER_ITEM');
        $this->expectExceptionMessage('negative');
        
        putenv('SHIPPING_BASE_COST_PER_ITEM=-2.0');
        new QuoteService();
    }

    /**
     * AC-6: When SHIPPING_WEIGHT_SURCHARGE_PER_KG=invalid is set, service fails to start with error message indicating non-numeric value for SHIPPING_WEIGHT_SURCHARGE_PER_KG
     */
    public function test_ac6_non_numeric_weight_surcharge_throws_exception_on_initialization()
    {
        $this->expectException(InvalidConfigurationException::class);
        $this->expectExceptionMessage('SHIPPING_WEIGHT_SURCHARGE_PER_KG');
        $this->expectExceptionMessage('non-numeric');
        
        putenv('SHIPPING_WEIGHT_SURCHARGE_PER_KG=invalid');
        new QuoteService();
    }

    /**
     * AC-7: When all environment variables are set to valid values, calculated shipping cost = max((numberOfItems * baseCost) + (totalWeight * surcharge), minimumCost)
     */
    public function test_ac7_all_configuration_values_combined_correctly()
    {
        putenv('SHIPPING_BASE_COST_PER_ITEM=3.99');
        putenv('SHIPPING_WEIGHT_SURCHARGE_PER_KG=1.5');
        putenv('SHIPPING_MINIMUM_ORDER_COST=20.0');
        
        $quoteService = new QuoteService();
        
        // Case 1: Calculated cost above minimum: (2 * 3.99) + (10 * 1.5) = 7.98 +15 =22.98 which is above min 20.0, returns 22.98
        $this->assertEquals(22.98, $quoteService->calculateShippingCost(2, 10));
        
        // Case 2: Calculated cost below minimum: (1 *3.99) + (2 *1.5)= 3.99+3=6.99 < 20, returns 20.0
        $this->assertEquals(20.0, $quoteService->calculateShippingCost(1, 2));
        
        // Case 3: Exactly equal to minimum
        putenv('SHIPPING_MINIMUM_ORDER_COST=6.99');
        $quoteService2 = new QuoteService();
        $this->assertEquals(6.99, $quoteService2->calculateShippingCost(1, 2));
    }

    /**
     * AC-8: Quote service documentation file includes all three new environment variables, their purpose, type, default values, and validation requirements
     */
    public function test_ac8_documentation_includes_new_environment_variables()
    {
        $readmeContent = file_get_contents(__DIR__ . '/../README.md');
        
        $this->assertStringContainsString('SHIPPING_BASE_COST_PER_ITEM', $readmeContent);
        $this->assertStringContainsString('SHIPPING_WEIGHT_SURCHARGE_PER_KG', $readmeContent);
        $this->assertStringContainsString('SHIPPING_MINIMUM_ORDER_COST', $readmeContent);
        
        // Check for required details for each variable
        $this->assertStringContainsString('float', $readmeContent);
        $this->assertStringContainsString('8.99', $readmeContent);
        $this->assertStringContainsString('0.0', $readmeContent);
        $this->assertStringContainsString('non-negative', $readmeContent);
        $this->assertStringContainsString('Base shipping cost', $readmeContent);
        $this->assertStringContainsString('weight surcharge', $readmeContent);
        $this->assertStringContainsString('minimum shipping cost', $readmeContent);
    }
}
