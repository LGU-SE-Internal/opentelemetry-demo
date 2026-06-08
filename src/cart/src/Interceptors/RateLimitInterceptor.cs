// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
using System.Net;
using AspNetCoreRateLimit;
using Grpc.Core;
using Grpc.Core.Interceptors;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace cart.Interceptors;

public class RateLimitInterceptor : Interceptor
{
    private readonly IIpRateLimitProcessor _rateLimitProcessor;
    private readonly IOptions<IpRateLimitOptions> _options;
    private readonly IHttpContextAccessor _httpContextAccessor;
    private readonly ILogger<RateLimitInterceptor> _logger;

    public RateLimitInterceptor(
        IIpRateLimitProcessor rateLimitProcessor,
        IOptions<IpRateLimitOptions> options,
        IHttpContextAccessor httpContextAccessor,
        ILogger<RateLimitInterceptor> logger)
    {
        _rateLimitProcessor = rateLimitProcessor;
        _options = options;
        _httpContextAccessor = httpContextAccessor;
        _logger = logger;
    }

    public override async Task<TResponse> UnaryServerHandler<TRequest, TResponse>(
        TRequest request,
        ServerCallContext context,
        UnaryServerMethod<TRequest, TResponse> continuation)
    {
        var httpContext = _httpContextAccessor.HttpContext;
        if (httpContext == null)
        {
            return await continuation(request, context);
        }

        var clientIp = GetClientIpAddress(httpContext);
        var endpoint = context.Method.Split('/').Last();
        var identity = new ClientRequestIdentity
        {
            ClientIp = clientIp.ToString(),
            Path = context.Method,
            HttpVerb = "POST" // gRPC always uses POST
        };

        try
        {
            var rateLimitCounter = await _rateLimitProcessor.ProcessRequestAsync(identity);
            if (rateLimitCounter.HasQuota)
            {
                return await continuation(request, context);
            }

            // Rate limit exceeded
            var retryAfter = (int)Math.Ceiling(rateLimitCounter.Reset.Subtract(DateTime.UtcNow).TotalSeconds);
            var rule = rateLimitCounter.Rule;

            // Log structured fields as required
            using (_logger.BeginScope(new Dictionary<string, object>
            {
                ["type"] = "rate_limit_exceeded",
                ["endpoint"] = endpoint,
                ["client_ip"] = clientIp.ToString(),
                ["limit"] = rule.Limit,
                ["window_seconds"] = rule.PeriodTimespan.Value.TotalSeconds,
                ["retry_after"] = retryAfter
            }))
            {
                _logger.LogWarning("Rate limit exceeded for endpoint {Endpoint} from client {ClientIp}. Limit: {Limit}, Window: {WindowSeconds}s, RetryAfter: {RetryAfter}s",
                    endpoint,
                    clientIp,
                    rule.Limit,
                    rule.PeriodTimespan.Value.TotalSeconds,
                    retryAfter
                );
            }

            var statusMessage = string.Format(_options.Value.QuotaExceededResponse.Content, endpoint);
            throw new RpcException(new Status(StatusCode.ResourceExhausted, statusMessage));
        }
        catch (Exception ex) when (ex is not RpcException)
        {
            // If rate limiting fails (e.g. Redis connection issue), disable rate limiting and log error
            _logger.LogError(ex, "Rate limiting processing failed, allowing request to proceed");
            return await continuation(request, context);
        }
    }

    private IPAddress GetClientIpAddress(HttpContext context)
    {
        var realIpHeader = _options.Value.RealIpHeader;
        if (!string.IsNullOrEmpty(realIpHeader) && context.Request.Headers.TryGetValue(realIpHeader, out var realIpValue))
        {
            var ipAddress = realIpValue.ToString().Split(',').FirstOrDefault()?.Trim();
            if (IPAddress.TryParse(ipAddress, out var address))
            {
                return address;
            }
        }

        return context.Connection.RemoteIpAddress ?? IPAddress.None;
    }
}
