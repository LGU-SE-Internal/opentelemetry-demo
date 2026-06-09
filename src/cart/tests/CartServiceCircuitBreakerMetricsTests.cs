using Xunit;
using Moq;
using OpenTelemetry;
using OpenTelemetry.Metrics;
using System.Collections.Generic;
using System.Threading.Tasks;
using System;
using System.Linq;
using Polly.CircuitBreaker;
using cart.Services;
using cart.CartStore;

namespace cart.Tests;

public class CartServiceCircuitBreakerMetricsTests : IDisposable
{
    private readonly MeterProvider _meterProvider;
    private readonly List<Metric> _exportedMetrics = new();

    public CartServiceCircuitBreakerMetricsTests()
    {
        _meterProvider = Sdk.CreateMeterProviderBuilder()
            .AddMeter("Cartservice.Resilience")
            .AddInMemoryExporter(_exportedMetrics)
            .Build();
    }

    public void Dispose()
    {
        _meterProvider.Dispose();
    }

    [Fact]
    public async Task test_ac1_closed_to_open_state_transition_emits_correct_metrics()
    {
        // Arrange
        var mockCartStore = new Mock<ICartStore>();
        mockCartStore.Setup(s => s.GetItemsAsync(It.IsAny<string>())).ThrowsAsync(new Exception("Redis failure"));
        
        var service = new CartService(mockCartStore.Object);
        var circuitBreaker = ((ValkeyCartStore)mockCartStore.Object).CircuitBreaker;

        // Act - trigger enough failures to open circuit
        for (int i = 0; i < 5; i++)
        {
            try { await service.GetCartAsync(new() { UserId = "test-user" }); } catch { }
        }

        // Assert state gauge is 1 (OPEN)
        _meterProvider.ForceFlush();
        var stateGauge = _exportedMetrics.First(m => m.Name == "resilience.circuit_breaker.state");
        Assert.Equal(1, stateGauge.MetricPoints.First().GetGaugeValue<int>());
        Assert.Equal("cartservice", stateGauge.MetricPoints.First().Attributes.First(a => a.Key == "service.name").Value);
        Assert.Equal("redis", stateGauge.MetricPoints.First().Attributes.First(a => a.Key == "resilience.circuit_breaker.name").Value);

        // Assert transition counter incremented for CLOSED -> OPEN
        var transitionCounter = _exportedMetrics.First(m => m.Name == "resilience.circuit_breaker.transitions");
        var transitionPoint = transitionCounter.MetricPoints.First(p => 
            p.Attributes.First(a => a.Key == "resilience.circuit_breaker.state.from").Value.ToString() == "CLOSED" &&
            p.Attributes.First(a => a.Key == "resilience.circuit_breaker.state.to").Value.ToString() == "OPEN");
        Assert.Equal(1, transitionPoint.GetSumValue<int>());
        Assert.Equal("cartservice", transitionPoint.Attributes.First(a => a.Key == "service.name").Value);
        Assert.Equal("redis", transitionPoint.Attributes.First(a => a.Key == "resilience.circuit_breaker.name").Value);
    }

    [Fact]
    public async Task test_ac2_open_to_halfopen_state_transition_emits_correct_metrics()
    {
        // Arrange - force circuit to open first
        var mockCartStore = new Mock<ICartStore>();
        mockCartStore.Setup(s => s.GetItemsAsync(It.IsAny<string>())).ThrowsAsync(new Exception("Redis failure"));
        var service = new CartService(mockCartStore.Object);
        var circuitBreaker = ((ValkeyCartStore)mockCartStore.Object).CircuitBreaker;
        
        for (int i = 0; i < 5; i++)
        {
            try { await service.GetCartAsync(new() { UserId = "test-user" }); } catch { }
        }
        Assert.Equal(CircuitState.Open, circuitBreaker.CircuitState);
        _exportedMetrics.Clear();

        // Act - wait for recovery period to pass so circuit goes to half-open
        await Task.Delay(TimeSpan.FromSeconds(30)); // matches default Polly recovery timeout
        try { await service.GetCartAsync(new() { UserId = "test-user" }); } catch { }

        // Assert state gauge is 2 (HALF_OPEN)
        _meterProvider.ForceFlush();
        var stateGauge = _exportedMetrics.First(m => m.Name == "resilience.circuit_breaker.state");
        Assert.Equal(2, stateGauge.MetricPoints.First().GetGaugeValue<int>());

        // Assert transition counter incremented for OPEN -> HALF_OPEN
        var transitionCounter = _exportedMetrics.First(m => m.Name == "resilience.circuit_breaker.transitions");
        var transitionPoint = transitionCounter.MetricPoints.First(p => 
            p.Attributes.First(a => a.Key == "resilience.circuit_breaker.state.from").Value.ToString() == "OPEN" &&
            p.Attributes.First(a => a.Key == "resilience.circuit_breaker.state.to").Value.ToString() == "HALF_OPEN");
        Assert.Equal(1, transitionPoint.GetSumValue<int>());
    }

    [Fact]
    public async Task test_ac3_halfopen_to_closed_state_transition_emits_correct_metrics()
    {
        // Arrange - get circuit to half-open state
        var mockCartStore = new Mock<ICartStore>();
        var failureCount = 0;
        mockCartStore.Setup(s => s.GetItemsAsync(It.IsAny<string>()))
            .ReturnsAsync(() => 
            {
                if (failureCount++ < 5) throw new Exception("Redis failure");
                return new Dictionary<string, int>();
            });
        
        var service = new CartService(mockCartStore.Object);
        var circuitBreaker = ((ValkeyCartStore)mockCartStore.Object).CircuitBreaker;
        
        // Open circuit
        for (int i = 0; i < 5; i++) { try { await service.GetCartAsync(new() { UserId = "test" }); } catch { } }
        // Wait for recovery
        await Task.Delay(TimeSpan.FromSeconds(30));
        _exportedMetrics.Clear();

        // Act - successful request moves circuit from half-open to closed
        await service.GetCartAsync(new() { UserId = "test" });

        // Assert state gauge is 0 (CLOSED)
        _meterProvider.ForceFlush();
        var stateGauge = _exportedMetrics.First(m => m.Name == "resilience.circuit_breaker.state");
        Assert.Equal(0, stateGauge.MetricPoints.First().GetGaugeValue<int>());

        // Assert transition counter incremented for HALF_OPEN -> CLOSED
        var transitionCounter = _exportedMetrics.First(m => m.Name == "resilience.circuit_breaker.transitions");
        var transitionPoint = transitionCounter.MetricPoints.First(p => 
            p.Attributes.First(a => a.Key == "resilience.circuit_breaker.state.from").Value.ToString() == "HALF_OPEN" &&
            p.Attributes.First(a => a.Key == "resilience.circuit_breaker.state.to").Value.ToString() == "CLOSED");
        Assert.Equal(1, transitionPoint.GetSumValue<int>());
    }

    [Fact]
    public async Task test_ac4_halfopen_to_open_state_transition_emits_correct_metrics()
    {
        // Arrange - get circuit to half-open state
        var mockCartStore = new Mock<ICartStore>();
        mockCartStore.Setup(s => s.GetItemsAsync(It.IsAny<string>())).ThrowsAsync(new Exception("Redis failure"));
        
        var service = new CartService(mockCartStore.Object);
        var circuitBreaker = ((ValkeyCartStore)mockCartStore.Object).CircuitBreaker;
        
        // Open circuit
        for (int i = 0; i < 5; i++) { try { await service.GetCartAsync(new() { UserId = "test" }); } catch { } }
        // Wait for recovery
        await Task.Delay(TimeSpan.FromSeconds(30));
        _exportedMetrics.Clear();

        // Act - failed request moves circuit from half-open back to open
        try { await service.GetCartAsync(new() { UserId = "test" }); } catch { }

        // Assert state gauge is 1 (OPEN)
        _meterProvider.ForceFlush();
        var stateGauge = _exportedMetrics.First(m => m.Name == "resilience.circuit_breaker.state");
        Assert.Equal(1, stateGauge.MetricPoints.First().GetGaugeValue<int>());

        // Assert transition counter incremented for HALF_OPEN -> OPEN
        var transitionCounter = _exportedMetrics.First(m => m.Name == "resilience.circuit_breaker.transitions");
        var transitionPoint = transitionCounter.MetricPoints.First(p => 
            p.Attributes.First(a => a.Key == "resilience.circuit_breaker.state.from").Value.ToString() == "HALF_OPEN" &&
            p.Attributes.First(a => a.Key == "resilience.circuit_breaker.state.to").Value.ToString() == "OPEN");
        Assert.Equal(1, transitionPoint.GetSumValue<int>());
    }

    [Fact]
    public async Task test_ac5_open_circuit_rejected_requests_emits_counter()
    {
        // Arrange - open circuit
        var mockCartStore = new Mock<ICartStore>();
        mockCartStore.Setup(s => s.GetItemsAsync(It.IsAny<string>())).ThrowsAsync(new Exception("Redis failure"));
        var service = new CartService(mockCartStore.Object);
        var circuitBreaker = ((ValkeyCartStore)mockCartStore.Object).CircuitBreaker;
        
        for (int i = 0; i < 5; i++) { try { await service.GetCartAsync(new() { UserId = "test" }); } catch { } }
        Assert.Equal(CircuitState.Open, circuitBreaker.CircuitState);
        _exportedMetrics.Clear();

        // Act - make requests while circuit is open (should be rejected)
        int rejectedCount = 3;
        for (int i = 0; i < rejectedCount; i++)
        {
            await Assert.ThrowsAsync<BrokenCircuitException>(() => 
                service.GetCartAsync(new() { UserId = "test" }));
        }

        // Assert rejected requests counter incremented
        _meterProvider.ForceFlush();
        var rejectedCounter = _exportedMetrics.First(m => m.Name == "resilience.circuit_breaker.requests_rejected");
        var rejectedPoint = rejectedCounter.MetricPoints.First(p => 
            p.Attributes.First(a => a.Key == "error.type").Value.ToString() == "circuit_breaker_open");
        Assert.Equal(rejectedCount, rejectedPoint.GetSumValue<int>());
        Assert.Equal("cartservice", rejectedPoint.Attributes.First(a => a.Key == "service.name").Value);
        Assert.Equal("redis", rejectedPoint.Attributes.First(a => a.Key == "resilience.circuit_breaker.name").Value);
    }
}
