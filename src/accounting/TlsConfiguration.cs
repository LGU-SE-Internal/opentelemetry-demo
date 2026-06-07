// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

using System.Security.Cryptography.X509Certificates;

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
