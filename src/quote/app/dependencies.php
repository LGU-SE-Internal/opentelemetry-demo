<?php
// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0



declare(strict_types=1);

use App\Application\Settings\SettingsInterface;
use DI\ContainerBuilder;
use OpenTelemetry\API\Globals;
use OpenTelemetry\Contrib\Logs\Monolog\Handler;
use Monolog\Logger;
use Psr\Container\ContainerInterface;
use Psr\Log\LoggerInterface;
use Psr\Log\LogLevel;

return function (ContainerBuilder $containerBuilder) {
    $containerBuilder->addDefinitions([
        LoggerInterface::class => function (ContainerInterface $c) {
            $settings = $c->get(SettingsInterface::class);
            $loggerSettings = $settings->get('logger');
            $handler = new Handler(
                Globals::loggerProvider(),
                LogLevel::INFO,
            );
            return new Logger($loggerSettings['name'], [$handler]);
        },
        'ResourceManager' => function (ContainerInterface $c) {
            // Resource manager to handle closing all persistent connections
            return new class {
                private $connections = [];
                
                public function addConnection($connection) {
                    $this->connections[] = $connection;
                }
                
                public function shutdown(): void {
                    foreach ($this->connections as $conn) {
                        if (method_exists($conn, 'close')) {
                            $conn->close();
                        }
                    }
                    $this->connections = [];
                }
            };
        },
    ]);
};
