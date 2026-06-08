<?php

namespace App\Application\Middleware;

use Psr\Http\Message\ServerRequestInterface as Request;
use Psr\Http\Server\RequestHandlerInterface as RequestHandler;
use Psr\Log\LoggerInterface;
use Slim\Psr7\Response;
use Google\Rpc\Code;

class QuoteRequestValidationMiddleware
{
    private const SUPPORTED_COUNTRIES = [
        'US', 'CA', 'AT', 'BE', 'BG', 'HR', 'CY', 'CZ', 'DK', 'EE', 'FI', 'FR',
        'DE', 'GR', 'HU', 'IE', 'IT', 'LV', 'LT', 'LU', 'MT', 'NL', 'PL', 'PT',
        'RO', 'SK', 'SI', 'ES', 'SE'
    ];

    private const ZIP_PATTERNS = [
        'US' => '/^\d{5}$/',
        'CA' => '/^[A-Za-z]\d[A-Za-z] \d[A-Za-z]\d$/',
        'AT' => '/^\d{4}$/',
        'BE' => '/^\d{4}$/',
        'BG' => '/^\d{4}$/',
        'HR' => '/^\d{5}$/',
        'CY' => '/^\d{4}$/',
        'CZ' => '/^\d{3} \d{2}$/',
        'DK' => '/^\d{4}$/',
        'EE' => '/^\d{5}$/',
        'FI' => '/^\d{5}$/',
        'FR' => '/^\d{5}$/',
        'DE' => '/^\d{5}$/',
        'GR' => '/^\d{3} \d{2}$/',
        'HU' => '/^\d{4}$/',
        'IE' => '/^[A-Za-z\d]{3} [A-Za-z\d]{4}$/',
        'IT' => '/^\d{5}$/',
        'LV' => '/^LV-\d{4}$/',
        'LT' => '/^(LT-)?\d{5}$/',
        'LU' => '/^L-\d{4}$/',
        'MT' => '/^[A-Za-z]{3} \d{2,4}$/',
        'NL' => '/^\d{4} [A-Za-z]{2}$/',
        'PL' => '/^\d{2}-\d{3}$/',
        'PT' => '/^\d{4}-\d{3}$/',
        'RO' => '/^\d{6}$/',
        'SK' => '/^\d{3} \d{2}$/',
        'SI' => '/^\d{4}$/',
        'ES' => '/^\d{5}$/',
        'SE' => '/^\d{3} \d{2}$/'
    ];

    public function __construct(
        private LoggerInterface $logger
    ) {}

    public function __invoke(Request $request, RequestHandler $handler): Response
    {
        $requestId = $request->getAttribute('request_id') ?? $request->getHeaderLine('X-Request-ID') ?: uniqid('quote_', true);
        $clientIp = $this->getClientIp($request);
        $body = $request->getParsedBody();

        $errors = [];

        // Validate required fields
        $requiredFields = ['item_count', 'total_weight_kg', 'destination_country', 'destination_zip_code'];
        foreach ($requiredFields as $field) {
            if (!isset($body[$field]) || $body[$field] === '') {
                $errors[$field] = "Missing required field: {$field}";
            }
        }

        if (!empty($errors)) {
            return $this->createErrorResponse(Code::INVALID_ARGUMENT, reset($errors), $requestId, $clientIp, array_keys($errors));
        }

        // Validate item_count is positive integer
        if (!is_int($body['item_count']) || $body['item_count'] <= 0) {
            $errors['item_count'] = "Item count must be a positive integer";
        }

        // Validate total_weight_kg is positive number
        if (!is_numeric($body['total_weight_kg']) || (float)$body['total_weight_kg'] <= 0) {
            $errors['total_weight_kg'] = "Total weight must be a positive number";
        }

        // Validate destination country is supported
        $country = strtoupper($body['destination_country']);
        if (!in_array($country, self::SUPPORTED_COUNTRIES)) {
            $errors['destination_country'] = "Delivery to country {$country} is not supported";
            $statusCode = Code::FAILED_PRECONDITION;
        } else {
            // Validate zip code format for country
            if (isset(self::ZIP_PATTERNS[$country])) {
                if (!preg_match(self::ZIP_PATTERNS[$country], $body['destination_zip_code'])) {
                    $errors['destination_zip_code'] = "Invalid zip code format for country {$country}";
                }
            }
        }

        if (!empty($errors)) {
            $statusCode ??= Code::INVALID_ARGUMENT;
            $errorMsg = reset($errors);
            return $this->createErrorResponse($statusCode, $errorMsg, $requestId, $clientIp, array_keys($errors));
        }

        // All validations passed, proceed to handler
        return $handler->handle($request);
    }

    private function getClientIp(Request $request): string
    {
        $xForwardedFor = $request->getHeaderLine('X-Forwarded-For');
        if (!empty($xForwardedFor)) {
            $ips = explode(',', $xForwardedFor);
            return trim($ips[0]);
        }
        
        $serverParams = $request->getServerParams();
        return $serverParams['REMOTE_ADDR'] ?? 'unknown';
    }

    private function createErrorResponse(int $grpcCode, string $errorMsg, string $requestId, string $clientIp, array $invalidFields): Response
    {
        // Log warning
        $this->logger->warning('Invalid quote calculation request', [
            'request_id' => $requestId,
            'client_ip' => $clientIp,
            'invalid_fields' => $invalidFields,
            'error_message' => $errorMsg,
            'timestamp' => gmdate('Y-m-d\TH:i:s\Z')
        ]);

        $response = new Response();
        $payload = json_encode([
            'error' => $grpcCode === Code::INVALID_ARGUMENT ? 'Invalid request parameter' : 'Failed precondition',
            'message' => $errorMsg,
            'requestId' => $requestId
        ]);

        $response->getBody()->write($payload);
        return $response
            ->withHeader('Content-Type', 'application/json')
            ->withStatus($grpcCode === Code::INVALID_ARGUMENT ? 400 : 412);
    }
}
