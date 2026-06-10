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
        $body = $request->getParsedBody() ?? [];
        $path = $request->getUri()->getPath();

        $errors = [];
        
        if ($path === '/quote') {
            // Validate required fields for /quote endpoint
            $requiredFields = ['item_weight', 'item_count', 'destination_zip'];
            $validationErrors = [];
            $invalidLogEntries = [];
            
            foreach ($requiredFields as $field) {
                if (!isset($body[$field]) || $body[$field] === '') {
                    $validationErrors[] = [
                        'field' => $field,
                        'error' => 'Field is required'
                    ];
                    $invalidLogEntries[] = [
                        'field' => $field,
                        'value' => isset($body[$field]) ? $body[$field] : null,
                        'error' => 'Field is required'
                    ];
                }
            }
            
            // Validate item_weight if present
            if (isset($body['item_weight'])) {
                $weight = $body['item_weight'];
                if (!is_numeric($weight)) {
                    $validationErrors[] = [
                        'field' => 'item_weight',
                        'error' => 'Must be a numeric value'
                    ];
                    $invalidLogEntries[] = [
                        'field' => 'item_weight',
                        'value' => $weight,
                        'error' => 'Must be a numeric value'
                    ];
                } else {
                    $floatWeight = (float)$weight;
                    if ($floatWeight < 0.01 || $floatWeight > 1000) {
                        $validationErrors[] = [
                            'field' => 'item_weight',
                            'error' => 'Must be between 0.01 and 1000 kg'
                        ];
                        $invalidLogEntries[] = [
                            'field' => 'item_weight',
                            'value' => $weight,
                            'error' => 'Must be between 0.01 and 1000 kg'
                        ];
                    }
                }
            }
            
            // Validate item_count if present
            if (isset($body['item_count'])) {
                $count = $body['item_count'];
                if (!is_int($count) && (!is_string($count) || !ctype_digit($count))) {
                    $validationErrors[] = [
                        'field' => 'item_count',
                        'error' => 'Must be an integer value'
                    ];
                    $invalidLogEntries[] = [
                        'field' => 'item_count',
                        'value' => $count,
                        'error' => 'Must be an integer value'
                    ];
                } else {
                    $intCount = (int)$count;
                    if ($intCount < 1 || $intCount > 100) {
                        $validationErrors[] = [
                            'field' => 'item_count',
                            'error' => 'Must be between 1 and 100'
                        ];
                        $invalidLogEntries[] = [
                            'field' => 'item_count',
                            'value' => $count,
                            'error' => 'Must be between 1 and 100'
                        ];
                    }
                }
            }
            
            // Validate destination_zip if present
            if (isset($body['destination_zip'])) {
                $zip = $body['destination_zip'];
                if (!preg_match('/^\d{5}$/', $zip)) {
                    $validationErrors[] = [
                        'field' => 'destination_zip',
                        'error' => 'Must be a 5-digit US zip code'
                    ];
                    $invalidLogEntries[] = [
                        'field' => 'destination_zip',
                        'value' => $zip,
                        'error' => 'Must be a 5-digit US zip code'
                    ];
                }
            }
            
            if (!empty($validationErrors)) {
                // Log the validation error at ERROR level as required
                $this->logger->error('Input validation failed', [
                    'request_id' => $requestId,
                    'client_ip' => $clientIp,
                    'invalid_fields' => $invalidLogEntries
                ]);
                
                $response = new Response();
                $payload = json_encode([
                    'error' => 'Bad Request',
                    'message' => 'Validation failed',
                    'details' => $validationErrors
                ]);
                $response->getBody()->write($payload);
                
                return $response
                    ->withHeader('Content-Type', 'application/json')
                    ->withStatus(400);
            }
        } elseif ($path === '/getquote') {
            // Validate required fields for /getquote endpoint
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
            } elseif ((float)$body['total_weight_kg'] > 1000) {
                $errors['total_weight_kg'] = "Total weight must not exceed 1000 kg";
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
        } elseif ($path === '/calculate') {
            // Validate required fields for /calculate endpoint
            $responseErrors = [];
            
            // Validate weight
            if (!isset($body['weight'])) {
                $responseErrors[] = [
                    'field' => 'weight',
                    'message' => 'Weight is required'
                ];
            } else {
                if (!is_numeric($body['weight'])) {
                    $responseErrors[] = [
                        'field' => 'weight',
                        'message' => 'Weight must be a numeric value'
                    ];
                } else {
                    $weight = (float)$body['weight'];
                    if ($weight <= 0) {
                        $responseErrors[] = [
                            'field' => 'weight',
                            'message' => 'Weight must be a positive value'
                        ];
                    } elseif ($weight > 1000) {
                        $responseErrors[] = [
                            'field' => 'weight',
                            'message' => 'Weight must not exceed 1000 kg'
                        ];
                    }
                }
            }
            
            // Validate destination zip code
            if (!isset($body['destination_zip'])) {
                $responseErrors[] = [
                    'field' => 'destination_zip',
                    'message' => 'Destination zip code is required'
                ];
            } else {
                $zip = $body['destination_zip'];
                if (!preg_match('/^\d{5}(-\d{4})?$/', $zip)) {
                    $responseErrors[] = [
                        'field' => 'destination_zip',
                        'message' => 'Invalid destination zip code format'
                    ];
                }
            }
            
            // Validate shipping method
            if (!isset($body['shipping_method'])) {
                $responseErrors[] = [
                    'field' => 'shipping_method',
                    'message' => 'Shipping method is required'
                ];
            } else {
                $allowedMethods = ['standard', 'express', 'overnight'];
                if (!in_array($body['shipping_method'], $allowedMethods)) {
                    $responseErrors[] = [
                        'field' => 'shipping_method',
                        'message' => 'Shipping method must be one of standard, express, overnight'
                    ];
                }
            }
            
            if (!empty($responseErrors)) {
                $response = new Response();
                $payload = json_encode(['errors' => $responseErrors]);
                $response->getBody()->write($payload);
                
                return $response
                    ->withHeader('Content-Type', 'application/json')
                    ->withStatus(400);
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
