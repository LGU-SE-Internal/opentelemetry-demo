<?php

use Grpc\Client;
use Opentelemetry\Demo\Proto\Shipping\V1\CalculateShippingQuoteRequest;
use Opentelemetry\Demo\Proto\Shipping\V1\QuoteServiceClient;
use Google\Rpc\Code;
use PHPUnit\Framework\TestCase;
use Psr\Log\LoggerInterface;

class QuoteServiceValidationTest extends TestCase
{
    private QuoteServiceClient $client;
    private LoggerInterface $logger;

    protected function setUp(): void
    {
        // Setup gRPC client
        $this->client = new QuoteServiceClient('quote-service:8080', [
            'credentials' => \Grpc\ChannelCredentials::createInsecure(),
        ]);
        // Setup logger mock for verifying logs
        $this->logger = $this->createMock(LoggerInterface::class);
    }

    // AC-1: Missing item_count returns INVALID_ARGUMENT
    public function test_ac1_missing_item_count_returns_invalid_argument(): void
    {
        $request = new CalculateShippingQuoteRequest();
        $request->setTotalWeightKg(1.5);
        $request->setDestinationCountry('US');
        $request->setDestinationZipCode('90210');

        $call = $this->client->CalculateShippingQuote($request);
        [$response, $status] = $call->wait();

        $this->assertEquals(Code::INVALID_ARGUMENT, $status->code);
        $this->assertEquals('Missing required field: item_count', $status->details);
    }

    // AC-2: Missing total_weight_kg returns INVALID_ARGUMENT
    public function test_ac2_missing_total_weight_returns_invalid_argument(): void
    {
        $request = new CalculateShippingQuoteRequest();
        $request->setItemCount(2);
        $request->setDestinationCountry('US');
        $request->setDestinationZipCode('90210');

        $call = $this->client->CalculateShippingQuote($request);
        [$response, $status] = $call->wait();

        $this->assertEquals(Code::INVALID_ARGUMENT, $status->code);
        $this->assertEquals('Missing required field: total_weight_kg', $status->details);
    }

    // AC-3: Missing destination_country returns INVALID_ARGUMENT
    public function test_ac3_missing_destination_country_returns_invalid_argument(): void
    {
        $request = new CalculateShippingQuoteRequest();
        $request->setItemCount(2);
        $request->setTotalWeightKg(1.5);
        $request->setDestinationZipCode('90210');

        $call = $this->client->CalculateShippingQuote($request);
        [$response, $status] = $call->wait();

        $this->assertEquals(Code::INVALID_ARGUMENT, $status->code);
        $this->assertEquals('Missing required field: destination_country', $status->details);
    }

    // AC-4: Missing destination_zip_code returns INVALID_ARGUMENT
    public function test_ac4_missing_destination_zip_code_returns_invalid_argument(): void
    {
        $request = new CalculateShippingQuoteRequest();
        $request->setItemCount(2);
        $request->setTotalWeightKg(1.5);
        $request->setDestinationCountry('US');

        $call = $this->client->CalculateShippingQuote($request);
        [$response, $status] = $call->wait();

        $this->assertEquals(Code::INVALID_ARGUMENT, $status->code);
        $this->assertEquals('Missing required field: destination_zip_code', $status->details);
    }

    // AC-5: item_count <=0 returns INVALID_ARGUMENT
    public function test_ac5_negative_item_count_returns_invalid_argument(): void
    {
        $request = new CalculateShippingQuoteRequest();
        $request->setItemCount(-1);
        $request->setTotalWeightKg(1.5);
        $request->setDestinationCountry('US');
        $request->setDestinationZipCode('90210');

        $call = $this->client->CalculateShippingQuote($request);
        [$response, $status] = $call->wait();

        $this->assertEquals(Code::INVALID_ARGUMENT, $status->code);
        $this->assertEquals('Item count must be a positive integer', $status->details);

        // Test zero case
        $request->setItemCount(0);
        $call = $this->client->CalculateShippingQuote($request);
        [$response, $status] = $call->wait();

        $this->assertEquals(Code::INVALID_ARGUMENT, $status->code);
        $this->assertEquals('Item count must be a positive integer', $status->details);
    }

    // AC-6: item_count non numeric returns INVALID_ARGUMENT
    public function test_ac6_non_numeric_item_count_returns_invalid_argument(): void
    {
        // Note: gRPC will enforce type, but test invalid values passed as wrong type
        $request = new CalculateShippingQuoteRequest();
        // Simulate invalid type passed (string instead of int)
        $request->setItemCount('invalid'); // @phpstan-ignore-line
        $request->setTotalWeightKg(1.5);
        $request->setDestinationCountry('US');
        $request->setDestinationZipCode('90210');

        $call = $this->client->CalculateShippingQuote($request);
        [$response, $status] = $call->wait();

        $this->assertEquals(Code::INVALID_ARGUMENT, $status->code);
        $this->assertEquals('Item count must be a positive integer', $status->details);
    }

    // AC-7: total_weight <= 0 returns INVALID_ARGUMENT
    public function test_ac7_negative_weight_returns_invalid_argument(): void
    {
        $request = new CalculateShippingQuoteRequest();
        $request->setItemCount(2);
        $request->setTotalWeightKg(-0.5);
        $request->setDestinationCountry('US');
        $request->setDestinationZipCode('90210');

        $call = $this->client->CalculateShippingQuote($request);
        [$response, $status] = $call->wait();

        $this->assertEquals(Code::INVALID_ARGUMENT, $status->code);
        $this->assertEquals('Total weight must be a positive number', $status->details);

        // Test zero case
        $request->setTotalWeightKg(0);
        $call = $this->client->CalculateShippingQuote($request);
        [$response, $status] = $call->wait();

        $this->assertEquals(Code::INVALID_ARGUMENT, $status->code);
        $this->assertEquals('Total weight must be a positive number', $status->details);
    }

    // AC-8: unsupported country returns FAILED_PRECONDITION
    public function test_ac8_unsupported_country_returns_failed_precondition(): void
    {
        $request = new CalculateShippingQuoteRequest();
        $request->setItemCount(2);
        $request->setTotalWeightKg(1.5);
        $request->setDestinationCountry('CN'); // China is not supported
        $request->setDestinationZipCode('100000');

        $call = $this->client->CalculateShippingQuote($request);
        [$response, $status] = $call->wait();

        $this->assertEquals(Code::FAILED_PRECONDITION, $status->code);
        $this->assertEquals('Delivery to country CN is not supported', $status->details);
    }

    // AC-9: invalid zip code format returns INVALID_ARGUMENT
    public function test_ac9_invalid_zip_format_returns_invalid_argument(): void
    {
        // Test US invalid zip (should be 5 digits)
        $request = new CalculateShippingQuoteRequest();
        $request->setItemCount(2);
        $request->setTotalWeightKg(1.5);
        $request->setDestinationCountry('US');
        $request->setDestinationZipCode('ABC12'); // Invalid format

        $call = $this->client->CalculateShippingQuote($request);
        [$response, $status] = $call->wait();

        $this->assertEquals(Code::INVALID_ARGUMENT, $status->code);
        $this->assertEquals('Invalid zip code format for country US', $status->details);
    }

    // AC-10: Invalid requests are logged at WARNING level with correct context
    public function test_ac10_invalid_requests_logged_as_warning(): void
    {
        // Mock logger to expect warning
        $this->logger->expects($this->once())
            ->method('warning')
            ->with(
                $this->equalTo('Invalid quote calculation request'),
                $this->callback(function ($context) {
                    $this->assertArrayHasKey('request_id', $context);
                    $this->assertArrayHasKey('client_ip', $context);
                    $this->assertArrayHasKey('invalid_fields', $context);
                    $this->assertArrayHasKey('error_message', $context);
                    $this->assertArrayHasKey('timestamp', $context);
                    return true;
                })
            );

        $request = new CalculateShippingQuoteRequest();
        $request->setItemCount(-1);
        $request->setTotalWeightKg(1.5);
        $request->setDestinationCountry('US');
        $request->setDestinationZipCode('90210');

        $call = $this->client->CalculateShippingQuote($request);
        [$response, $status] = $call->wait();

        $this->assertEquals(Code::INVALID_ARGUMENT, $status->code);
    }

    // AC-11: Valid requests proceed to normal calculation flow
    public function test_ac11_valid_request_proceeds_to_calculation(): void
    {
        $request = new CalculateShippingQuoteRequest();
        $request->setItemCount(2);
        $request->setTotalWeightKg(1.5);
        $request->setDestinationCountry('US');
        $request->setDestinationZipCode('90210');

        $call = $this->client->CalculateShippingQuote($request);
        [$response, $status] = $call->wait();

        $this->assertEquals(Code::OK, $status->code);
        $this->assertNotNull($response);
        $this->assertGreaterThan(0, $response->getShippingCostUsd());
    }
}
