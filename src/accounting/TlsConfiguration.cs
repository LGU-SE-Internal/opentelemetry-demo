// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

using System.Security.Cryptography.X509Certificates;
using Microsoft.Extensions.Logging;

namespace Accounting;

internal static class TlsConfiguration
{
    private const string KAFKA_TLS_ENABLED = "KAFKA_TLS_ENABLED";
    private const string KAFKA_TLS_CA_CERT_PATH = "KAFKA_TLS_CA_CERT_PATH";
    private const string KAFKA_TLS_CLIENT_CERT_PATH = "KAFKA_TLS_CLIENT_CERT_PATH";
    private const string KAFKA_TLS_CLIENT_KEY_PATH = "KAFKA_TLS_CLIENT_KEY_PATH";
    
    private const string POSTGRES_TLS_ENABLED = "POSTGRES_TLS_ENABLED";
    private const string POSTGRES_TLS_CA_CERT_PATH = "POSTGRES_TLS_CA_CERT_PATH";
    private const string POSTGRES_TLS_CLIENT_CERT_PATH = "POSTGRES_TLS_CLIENT_CERT_PATH";
    private const string POSTGRES_TLS_CLIENT_KEY_PATH = "POSTGRES_TLS_CLIENT_KEY_PATH";
    
    private const string ACCOUNTING_SERVICE_TLS_CERT_PATH = "ACCOUNTING_SERVICE_TLS_CERT_PATH";
    private const string ACCOUNTING_SERVICE_TLS_KEY_PATH = "ACCOUNTING_SERVICE_TLS_KEY_PATH";
    private const string ACCOUNTING_SERVICE_MTLS_CA_CERT_PATH = "ACCOUNTING_SERVICE_MTLS_CA_CERT_PATH";

    public static (bool TlsEnabled, bool MtlsEnabled, string CertPath, string KeyPath, string CaCertPath) GetHttpServerTlsConfig(ILogger logger)
    {
        var certPath = Environment.GetEnvironmentVariable(ACCOUNTING_SERVICE_TLS_CERT_PATH) ?? string.Empty;
        var keyPath = Environment.GetEnvironmentVariable(ACCOUNTING_SERVICE_TLS_KEY_PATH) ?? string.Empty;
        var caCertPath = Environment.GetEnvironmentVariable(ACCOUNTING_SERVICE_MTLS_CA_CERT_PATH) ?? string.Empty;

        var hasCert = !string.IsNullOrWhiteSpace(certPath);
        var hasKey = !string.IsNullOrWhiteSpace(keyPath);
        var hasCaCert = !string.IsNullOrWhiteSpace(caCertPath);

        if (hasCert != hasKey)
        {
            throw new InvalidTlsConfigurationException("Both ACCOUNTING_SERVICE_TLS_CERT_PATH and ACCOUNTING_SERVICE_TLS_KEY_PATH must be provided when configuring TLS for HTTP server");
        }

        if (!hasCert)
        {
            if (hasCaCert)
            {
                logger.LogWarning("WARNING: ACCOUNTING_SERVICE_MTLS_CA_CERT_PATH provided without TLS cert/key, mTLS configuration will be ignored");
            }
            return (false, false, string.Empty, string.Empty, string.Empty);
        }

        // Validate cert and key files exist and are valid
        if (!File.Exists(certPath))
        {
            throw new InvalidTlsConfigurationException($"TLS certificate file not found at path: {certPath}");
        }
        if (!File.Exists(keyPath))
        {
            throw new InvalidTlsConfigurationException($"TLS private key file not found at path: {keyPath}");
        }

        try
        {
            _ = X509Certificate2.CreateFromPemFile(certPath, keyPath);
        }
        catch (Exception ex)
        {
            throw new InvalidTlsConfigurationException($"TLS certificate/key pair is invalid: {ex.Message}", ex);
        }

        bool mtlsEnabled = false;
        if (hasCaCert)
        {
            if (!File.Exists(caCertPath))
            {
                throw new InvalidTlsConfigurationException($"mTLS CA certificate file not found at path: {caCertPath}");
            }
            try
            {
                _ = new X509Certificate2(caCertPath);
            }
            catch (Exception ex)
            {
                throw new InvalidTlsConfigurationException($"mTLS CA certificate file is invalid: {ex.Message}", ex);
            }
            mtlsEnabled = true;
        }

        return (true, mtlsEnabled, certPath, keyPath, caCertPath);
    }

    public static (bool Enabled, string CaCertPath, string ClientCertPath, string ClientKeyPath) GetKafkaTlsConfig()
    {
        var enabled = bool.TryParse(Environment.GetEnvironmentVariable(KAFKA_TLS_ENABLED), out var result) && result;
        
        if (!enabled)
        {
            return (false, string.Empty, string.Empty, string.Empty);
        }

        var caCertPath = Environment.GetEnvironmentVariable(KAFKA_TLS_CA_CERT_PATH) ?? string.Empty;
        var clientCertPath = Environment.GetEnvironmentVariable(KAFKA_TLS_CLIENT_CERT_PATH) ?? string.Empty;
        var clientKeyPath = Environment.GetEnvironmentVariable(KAFKA_TLS_CLIENT_KEY_PATH) ?? string.Empty;

        ValidateTlsConfig(caCertPath, clientCertPath, clientKeyPath, "Kafka");

        return (enabled, caCertPath, clientCertPath, clientKeyPath);
    }
    
    public static (bool Enabled, string CaCertPath, string ClientCertPath, string ClientKeyPath) GetPostgresTlsConfig()
    {
        var enabled = bool.TryParse(Environment.GetEnvironmentVariable(POSTGRES_TLS_ENABLED), out var result) && result;
        
        if (!enabled)
        {
            return (false, string.Empty, string.Empty, string.Empty);
        }

        var caCertPath = Environment.GetEnvironmentVariable(POSTGRES_TLS_CA_CERT_PATH) ?? string.Empty;
        var clientCertPath = Environment.GetEnvironmentVariable(POSTGRES_TLS_CLIENT_CERT_PATH) ?? string.Empty;
        var clientKeyPath = Environment.GetEnvironmentVariable(POSTGRES_TLS_CLIENT_KEY_PATH) ?? string.Empty;

        ValidateTlsConfig(caCertPath, clientCertPath, clientKeyPath, "PostgreSQL");

        return (enabled, caCertPath, clientCertPath, clientKeyPath);
    }

    private static void ValidateTlsConfig(string caCertPath, string clientCertPath, string clientKeyPath, string serviceName)
    {
        // Validate CA cert
        if (string.IsNullOrWhiteSpace(caCertPath))
        {
            throw new InvalidTlsConfigurationException($"{serviceName} TLS is enabled but {serviceName.ToUpper()}_TLS_CA_CERT_PATH is not provided");
        }

        if (!File.Exists(caCertPath))
        {
            throw new InvalidTlsConfigurationException($"{serviceName} CA certificate file not found at path: {caCertPath}");
        }

        try
        {
            var caCert = new X509Certificate2(caCertPath);
        }
        catch (Exception ex)
        {
            throw new InvalidTlsConfigurationException($"{serviceName} CA certificate file is invalid: {ex.Message}", ex);
        }

        // Validate client cert/key pair
        var hasClientCert = !string.IsNullOrWhiteSpace(clientCertPath);
        var hasClientKey = !string.IsNullOrWhiteSpace(clientKeyPath);

        if (hasClientCert != hasClientKey)
        {
            throw new InvalidTlsConfigurationException($"Both {serviceName.ToUpper()}_TLS_CLIENT_CERT_PATH and {serviceName.ToUpper()}_TLS_CLIENT_KEY_PATH must be provided when using mTLS");
        }

        if (hasClientCert)
        {
            if (!File.Exists(clientCertPath))
            {
                throw new InvalidTlsConfigurationException($"{serviceName} client certificate file not found at path: {clientCertPath}");
            }
            
            if (!File.Exists(clientKeyPath))
            {
                throw new InvalidTlsConfigurationException($"{serviceName} client private key file not found at path: {clientKeyPath}");
            }

            try
            {
                var clientCert = X509Certificate2.CreateFromPemFile(clientCertPath, clientKeyPath);
            }
            catch (Exception ex)
            {
                throw new InvalidTlsConfigurationException($"{serviceName} client certificate/key pair is invalid: {ex.Message}", ex);
            }
        }
    }
}
