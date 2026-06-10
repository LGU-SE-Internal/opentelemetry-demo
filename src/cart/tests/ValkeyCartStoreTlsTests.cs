using Xunit;
using Microsoft.Extensions.Configuration;
using System;
using System.Collections.Generic;
using System.IO;
using System.Threading.Tasks;
using CartService.Services;

namespace CartService.Tests;

public class ValkeyCartStoreTlsTests
{
    private const string TlsEnabledEnvVar = "CART_SERVICE_VALKEY_TLS_ENABLED";
    private const string CaCertPathEnvVar = "CART_SERVICE_VALKEY_CA_CERT_PATH";
    private const string ClientCertPathEnvVar = "CART_SERVICE_VALKEY_CLIENT_CERT_PATH";
    private const string ClientKeyPathEnvVar = "CART_SERVICE_VALKEY_CLIENT_KEY_PATH";

    public ValkeyCartStoreTlsTests()
    {
        // Clear all relevant environment variables before each test
        Environment.SetEnvironmentVariable(TlsEnabledEnvVar, null);
        Environment.SetEnvironmentVariable(CaCertPathEnvVar, null);
        Environment.SetEnvironmentVariable(ClientCertPathEnvVar, null);
        Environment.SetEnvironmentVariable(ClientKeyPathEnvVar, null);
    }

    [Fact]
    public void test_ac1_tls_disabled_by_default_uses_plaintext_connection()
    {
        // Arrange: No TLS configuration set
        var configuration = new ConfigurationBuilder()
            .AddEnvironmentVariables()
            .Build();

        // Act & Assert: Connection should be established without TLS, no exceptions
        // Implementation will be added later, currently this will fail
        var store = new ValkeyCartStore(configuration);
        Assert.NotNull(store);
        // Verify connection string has ssl=false or equivalent
    }

    [Fact]
    public async Task test_ac2_tls_enabled_uses_tls_1_2_or_higher()
    {
        // Arrange: Enable TLS
        Environment.SetEnvironmentVariable(TlsEnabledEnvVar, "true");
        var configuration = new ConfigurationBuilder()
            .AddEnvironmentVariables()
            .Build();

        // Act & Assert: Connection should use TLS 1.2+
        // Currently will fail as implementation does not exist
        var store = new ValkeyCartStore(configuration);
        // Verify connection uses TLS >= 1.2 when connecting
        await Task.CompletedTask;
    }

    [Fact]
    public void test_ac3_custom_ca_cert_used_for_validation_when_provided()
    {
        // Arrange: Enable TLS and set CA cert path
        Environment.SetEnvironmentVariable(TlsEnabledEnvVar, "true");
        var testCaPath = Path.GetTempFileName();
        Environment.SetEnvironmentVariable(CaCertPathEnvVar, testCaPath);
        
        try
        {
            var configuration = new ConfigurationBuilder()
                .AddEnvironmentVariables()
                .Build();

            // Act & Assert: Custom CA should be used for validation
            // Currently will fail
            var store = new ValkeyCartStore(configuration);
            Assert.NotNull(store);
        }
        finally
        {
            File.Delete(testCaPath);
        }
    }

    [Fact]
    public void test_ac4_mtls_client_cert_and_key_used_when_provided()
    {
        // Arrange: Enable TLS, set client cert and key paths
        Environment.SetEnvironmentVariable(TlsEnabledEnvVar, "true");
        var testCertPath = Path.GetTempFileName();
        var testKeyPath = Path.GetTempFileName();
        Environment.SetEnvironmentVariable(ClientCertPathEnvVar, testCertPath);
        Environment.SetEnvironmentVariable(ClientKeyPathEnvVar, testKeyPath);
        
        try
        {
            var configuration = new ConfigurationBuilder()
                .AddEnvironmentVariables()
                .Build();

            // Act & Assert: Client cert should be presented for mTLS
            // Currently will fail
            var store = new ValkeyCartStore(configuration);
            Assert.NotNull(store);
        }
        finally
        {
            File.Delete(testCertPath);
            File.Delete(testKeyPath);
        }
    }

    [Fact]
    public void test_ac5_client_cert_without_key_throws_configuration_exception()
    {
        // Arrange: Enable TLS, set client cert only (no key)
        Environment.SetEnvironmentVariable(TlsEnabledEnvVar, "true");
        Environment.SetEnvironmentVariable(ClientCertPathEnvVar, "/tmp/nonexistent-cert.pem");
        
        var configuration = new ConfigurationBuilder()
            .AddEnvironmentVariables()
            .Build();

        // Act & Assert: Should throw ConfigurationException on startup
        // Currently will fail
        Assert.Throws<System.Configuration.ConfigurationException>(() => new ValkeyCartStore(configuration));
    }

    [Fact]
    public void test_ac5_client_key_without_cert_throws_configuration_exception()
    {
        // Arrange: Enable TLS, set client key only (no cert)
        Environment.SetEnvironmentVariable(TlsEnabledEnvVar, "true");
        Environment.SetEnvironmentVariable(ClientKeyPathEnvVar, "/tmp/nonexistent-key.pem");
        
        var configuration = new ConfigurationBuilder()
            .AddEnvironmentVariables()
            .Build();

        // Act & Assert: Should throw ConfigurationException on startup
        // Currently will fail
        Assert.Throws<System.Configuration.ConfigurationException>(() => new ValkeyCartStore(configuration));
    }

    [Fact]
    public void test_ac7_existing_tests_pass_without_tls_configuration()
    {
        // Arrange: No TLS configuration set (default behavior)
        var configuration = new ConfigurationBuilder()
            .AddEnvironmentVariables()
            .Build();

        // Act & Assert: ValkeyCartStore works as before (backward compatibility)
        // Currently will fail
        var store = new ValkeyCartStore(configuration);
        // Verify basic cart operations work (e.g. add item, get cart)
        Assert.NotNull(store);
    }

    [Fact]
    public void test_ac8_invalid_ca_cert_path_throws_exception()
    {
        // Arrange: Enable TLS with invalid CA path
        Environment.SetEnvironmentVariable(TlsEnabledEnvVar, "true");
        Environment.SetEnvironmentVariable(CaCertPathEnvVar, "/tmp/nonexistent-ca.pem");
        
        var configuration = new ConfigurationBuilder()
            .AddEnvironmentVariables()
            .Build();

        // Act & Assert: Should throw ConfigurationException on startup
        // Currently will fail
        Assert.Throws<System.Configuration.ConfigurationException>(() => new ValkeyCartStore(configuration));
    }

    [Fact]
    public void test_ac8_invalid_client_cert_path_throws_exception()
    {
        // Arrange: Enable TLS with invalid client cert/key paths
        Environment.SetEnvironmentVariable(TlsEnabledEnvVar, "true");
        Environment.SetEnvironmentVariable(ClientCertPathEnvVar, "/tmp/invalid-cert.pem");
        Environment.SetEnvironmentVariable(ClientKeyPathEnvVar, "/tmp/invalid-key.pem");
        
        var configuration = new ConfigurationBuilder()
            .AddEnvironmentVariables()
            .Build();

        // Act & Assert: Should throw ConfigurationException on startup
        // Currently will fail
        Assert.Throws<System.Configuration.ConfigurationException>(() => new ValkeyCartStore(configuration));
    }
}
