using System;
using System.Collections.Generic;
using System.Net.Http;
using System.Net.Security;
using System.Security.Cryptography.X509Certificates;
using System.Threading.Tasks;
using Grpc.Core;
using Grpc.Net.Client;
using Xunit;
using Cart;

namespace CartServiceTests;

public class CartServiceTlsTests : IDisposable
{
    private const string DefaultServiceAddress = "https://localhost:8080";
    private const string PlaintextServiceAddress = "http://localhost:8080";
    private readonly X509Certificate2 _testServerCert;
    private readonly X509Certificate2 _testClientCert;
    private readonly X509Certificate2 _testCaCert;

    public CartServiceTlsTests()
    {
        // Load test certificates (these would be pre-generated for test environment)
        _testCaCert = new X509Certificate2("test-ca.crt");
        _testServerCert = new X509Certificate2("test-server.crt", "test-password");
        _testClientCert = new X509Certificate2("test-client.crt", "test-password");
    }

    public void Dispose()
    {
        _testServerCert.Dispose();
        _testClientCert.Dispose();
        _testCaCert.Dispose();
    }

    [Fact]
    public async Task Test_ac1_default_config_serves_unencrypted_traffic()
    {
        // Arrange: No TLS environment variables set
        Environment.SetEnvironmentVariable("CART_SERVICE_TLS_ENABLED", "false");
        Environment.SetEnvironmentVariable("CART_SERVICE_TLS_CERT_PATH", "");
        Environment.SetEnvironmentVariable("CART_SERVICE_TLS_KEY_PATH", "");
        Environment.SetEnvironmentVariable("CART_SERVICE_MTLS_ENABLED", "false");
        Environment.SetEnvironmentVariable("CART_SERVICE_MTLS_CA_CERT_PATH", "");

        // Act: Start service and attempt plaintext connection
        using var channel = GrpcChannel.ForAddress(PlaintextServiceAddress, new GrpcChannelOptions
        {
            HttpHandler = new SocketsHttpHandler
            {
                SslOptions = new SslClientAuthenticationOptions
                {
                    RemoteCertificateValidationCallback = (sender, cert, chain, errors) => true
                }
            }
        });
        var client = new CartService.CartServiceClient(channel);

        // Assert: Service starts successfully and accepts plaintext requests
        var response = await client.AddItemAsync(new AddItemRequest
        {
            UserId = "test-user",
            Item = new CartItem { ProductId = "test-product", Quantity = 1 }
        });
        Assert.NotNull(response);
    }

    [Fact]
    public async Task Test_ac2_tls_enabled_only_accepts_encrypted_connections()
    {
        // Arrange: TLS enabled with valid cert/key paths
        Environment.SetEnvironmentVariable("CART_SERVICE_TLS_ENABLED", "true");
        Environment.SetEnvironmentVariable("CART_SERVICE_TLS_CERT_PATH", "test-server.crt");
        Environment.SetEnvironmentVariable("CART_SERVICE_TLS_KEY_PATH", "test-server.key");
        Environment.SetEnvironmentVariable("CART_SERVICE_MTLS_ENABLED", "false");

        // Act 1: Attempt plaintext connection
        using var plaintextChannel = GrpcChannel.ForAddress(PlaintextServiceAddress);
        var plaintextClient = new CartService.CartServiceClient(plaintextChannel);
        var plaintextException = await Assert.ThrowsAsync<RpcException>(() => 
            plaintextClient.AddItemAsync(new AddItemRequest { UserId = "test-user" }).ResponseAsync);
        
        // Assert 1: Plaintext connection is rejected
        Assert.Equal(StatusCode.Unavailable, plaintextException.StatusCode);

        // Act 2: Attempt TLS 1.2+ encrypted connection
        var handler = new SocketsHttpHandler
        {
            SslOptions = new SslClientAuthenticationOptions
            {
                ApplicationProtocols = new List<SslApplicationProtocol> { SslApplicationProtocol.Http2 },
                RemoteCertificateValidationCallback = (sender, cert, chain, errors) =>
                {
                    chain.ChainPolicy.TrustMode = X509ChainTrustMode.CustomRootTrust;
                    chain.ChainPolicy.CustomTrustStore.Add(_testCaCert);
                    return chain.Build((X509Certificate2)cert);
                }
            }
        };
        using var tlsChannel = GrpcChannel.ForAddress(DefaultServiceAddress, new GrpcChannelOptions { HttpHandler = handler });
        var tlsClient = new CartService.CartServiceClient(tlsChannel);
        var response = await tlsClient.AddItemAsync(new AddItemRequest
        {
            UserId = "test-user",
            Item = new CartItem { ProductId = "test-product", Quantity = 1 }
        });

        // Assert 2: TLS connection is accepted
        Assert.NotNull(response);
    }

    [Fact]
    public void Test_ac3_tls_enabled_missing_cert_path_throws_config_exception()
    {
        // Arrange: TLS enabled but cert path empty
        Environment.SetEnvironmentVariable("CART_SERVICE_TLS_ENABLED", "true");
        Environment.SetEnvironmentVariable("CART_SERVICE_TLS_CERT_PATH", "");
        Environment.SetEnvironmentVariable("CART_SERVICE_TLS_KEY_PATH", "test-server.key");

        // Act & Assert: Service fails to start with InvalidConfigurationException
        var exception = Assert.Throws<InvalidOperationException>(() => 
            Program.CreateHostBuilder(Array.Empty<string>()).Build().RunAsync());
        Assert.Contains("CART_SERVICE_TLS_CERT_PATH is required when TLS is enabled", exception.Message);
    }

    [Fact]
    public void Test_ac4_tls_enabled_nonexistent_cert_path_throws_config_exception()
    {
        // Arrange: TLS enabled with non-existent cert path
        Environment.SetEnvironmentVariable("CART_SERVICE_TLS_ENABLED", "true");
        Environment.SetEnvironmentVariable("CART_SERVICE_TLS_CERT_PATH", "nonexistent.crt");
        Environment.SetEnvironmentVariable("CART_SERVICE_TLS_KEY_PATH", "test-server.key");

        // Act & Assert: Service fails to start with InvalidOperationException
        var exception = Assert.Throws<InvalidOperationException>(() => 
            Program.CreateHostBuilder(Array.Empty<string>()).Build().RunAsync());
        Assert.Contains("CART_SERVICE_TLS_CERT_PATH points to non-existent file", exception.Message);
    }

    [Fact]
    public void Test_ac5_mtls_enabled_without_tls_throws_config_exception()
    {
        // Arrange: mTLS enabled but TLS disabled
        Environment.SetEnvironmentVariable("CART_SERVICE_TLS_ENABLED", "false");
        Environment.SetEnvironmentVariable("CART_SERVICE_MTLS_ENABLED", "true");
        Environment.SetEnvironmentVariable("CART_SERVICE_MTLS_CA_CERT_PATH", "test-ca.crt");

        // Act & Assert: Service fails to start with InvalidOperationException
        var exception = Assert.Throws<InvalidOperationException>(() => 
            Program.CreateHostBuilder(Array.Empty<string>()).Build().RunAsync());
        Assert.Contains("CART_SERVICE_MTLS_ENABLED requires CART_SERVICE_TLS_ENABLED to be true", exception.Message);
    }

    [Fact]
    public async Task Test_ac6_mtls_enabled_validates_client_certificates()
    {
        // Arrange: TLS and mTLS enabled with valid CA path
        Environment.SetEnvironmentVariable("CART_SERVICE_TLS_ENABLED", "true");
        Environment.SetEnvironmentVariable("CART_SERVICE_TLS_CERT_PATH", "test-server.crt");
        Environment.SetEnvironmentVariable("CART_SERVICE_TLS_KEY_PATH", "test-server.key");
        Environment.SetEnvironmentVariable("CART_SERVICE_MTLS_ENABLED", "true");
        Environment.SetEnvironmentVariable("CART_SERVICE_MTLS_CA_CERT_PATH", "test-ca.crt");

        // Act 1: Request with no client certificate
        var noCertHandler = new SocketsHttpHandler
        {
            SslOptions = new SslClientAuthenticationOptions
            {
                ApplicationProtocols = new List<SslApplicationProtocol> { SslApplicationProtocol.Http2 },
                RemoteCertificateValidationCallback = (sender, cert, chain, errors) => true
            }
        };
        using var noCertChannel = GrpcChannel.ForAddress(DefaultServiceAddress, new GrpcChannelOptions { HttpHandler = noCertHandler });
        var noCertClient = new CartService.CartServiceClient(noCertChannel);
        var noCertException = await Assert.ThrowsAsync<RpcException>(() => 
            noCertClient.AddItemAsync(new AddItemRequest { UserId = "test-user" }).ResponseAsync);
        
        // Assert 1: Request rejected with Unauthenticated status
        Assert.Equal(StatusCode.Unauthenticated, noCertException.StatusCode);

        // Act 2: Request with invalid client certificate
        var invalidCertHandler = new SocketsHttpHandler
        {
            SslOptions = new SslClientAuthenticationOptions
            {
                ApplicationProtocols = new List<SslApplicationProtocol> { SslApplicationProtocol.Http2 },
                ClientCertificates = new X509CertificateCollection { new X509Certificate2("invalid-client.crt") },
                RemoteCertificateValidationCallback = (sender, cert, chain, errors) => true
            }
        };
        using var invalidCertChannel = GrpcChannel.ForAddress(DefaultServiceAddress, new GrpcChannelOptions { HttpHandler = invalidCertHandler });
        var invalidCertClient = new CartService.CartServiceClient(invalidCertChannel);
        var invalidCertException = await Assert.ThrowsAsync<RpcException>(() => 
            invalidCertClient.AddItemAsync(new AddItemRequest { UserId = "test-user" }).ResponseAsync);
        
        // Assert 2: Request rejected with Unauthenticated status
        Assert.Equal(StatusCode.Unauthenticated, invalidCertException.StatusCode);

        // Act 3: Request with valid client certificate
        var validCertHandler = new SocketsHttpHandler
        {
            SslOptions = new SslClientAuthenticationOptions
            {
                ApplicationProtocols = new List<SslApplicationProtocol> { SslApplicationProtocol.Http2 },
                ClientCertificates = new X509CertificateCollection { _testClientCert },
                RemoteCertificateValidationCallback = (sender, cert, chain, errors) =>
                {
                    chain.ChainPolicy.TrustMode = X509ChainTrustMode.CustomRootTrust;
                    chain.ChainPolicy.CustomTrustStore.Add(_testCaCert);
                    return chain.Build((X509Certificate2)cert);
                }
            }
        };
        using var validCertChannel = GrpcChannel.ForAddress(DefaultServiceAddress, new GrpcChannelOptions { HttpHandler = validCertHandler });
        var validCertClient = new CartService.CartServiceClient(validCertChannel);
        var response = await validCertClient.AddItemAsync(new AddItemRequest
        {
            UserId = "test-user",
            Item = new CartItem { ProductId = "test-product", Quantity = 1 }
        });

        // Assert 3: Request accepted and processed
        Assert.NotNull(response);
    }

    [Fact]
    public void Test_ac7_mtls_enabled_invalid_ca_cert_throws_config_exception()
    {
        // Arrange: mTLS enabled with invalid CA cert path
        Environment.SetEnvironmentVariable("CART_SERVICE_TLS_ENABLED", "true");
        Environment.SetEnvironmentVariable("CART_SERVICE_TLS_CERT_PATH", "test-server.crt");
        Environment.SetEnvironmentVariable("CART_SERVICE_TLS_KEY_PATH", "test-server.key");
        Environment.SetEnvironmentVariable("CART_SERVICE_MTLS_ENABLED", "true");
        Environment.SetEnvironmentVariable("CART_SERVICE_MTLS_CA_CERT_PATH", "invalid-ca.crt");

        // Act & Assert: Service fails to start with InvalidOperationException
        var exception = Assert.Throws<InvalidOperationException>(() => 
            Program.CreateHostBuilder(Array.Empty<string>()).Build().RunAsync());
        Assert.Contains("CART_SERVICE_MTLS_CA_CERT_PATH contains invalid PEM format", exception.Message);
    }
}
