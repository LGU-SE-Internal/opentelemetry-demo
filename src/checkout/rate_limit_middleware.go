package main

import (
	"context"
	"encoding/json"
	"net"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"golang.org/x/time/rate"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/peer"
	"google.golang.org/grpc/status"
)

var (
	rateLimitExceededTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "checkout_service_rate_limit_exceeded_total",
			Help: "Total number of requests rejected due to rate limiting",
		},
		[]string{"client_ip", "endpoint"},
	)
	rateLimitAllowedTotal = promauto.NewCounterVec(
		prometheus.CounterOpts{
			Name: "checkout_service_rate_limit_allowed_total",
			Help: "Total number of requests allowed past rate limiting",
		},
		[]string{"client_ip", "endpoint"},
	)

	defaultRateLimitEnabled = true
	defaultRateLimitRPM     = 10
)

func init() {
	// Load environment variables
	if enabledStr := os.Getenv("CHECKOUT_SERVICE_RATE_LIMIT_ENABLED"); enabledStr != "" {
		if parsed, err := strconv.ParseBool(enabledStr); err == nil {
			defaultRateLimitEnabled = parsed
		}
	}
	if rpmStr := os.Getenv("CHECKOUT_SERVICE_RATE_LIMIT_RPM"); rpmStr != "" {
		if parsed, err := strconv.Atoi(rpmStr); err == nil && parsed > 0 {
			defaultRateLimitRPM = parsed
		}
	}
}

type clientLimiter struct {
	limiter  *rate.Limiter
	lastSeen time.Time
}

type RateLimiter struct {
	rps     rate.Limit
	burst   int
	clients map[string]*clientLimiter
	mu      sync.RWMutex
	enabled bool
}

// NewRateLimiter creates a new RateLimiter instance with given requests per minute and enabled status
func NewRateLimiter(rpm int, enabled bool) *RateLimiter {
	rps := rate.Limit(float64(rpm) / 60.0)
	rl := &RateLimiter{
		rps:     rps,
		burst:   rpm, // Allow burst up to full RPM per minute
		clients: make(map[string]*clientLimiter),
		enabled: enabled,
	}

	// Start background goroutine to clean up old client entries
	go func() {
		for {
			time.Sleep(time.Hour)
			rl.mu.Lock()
			for ip, limiter := range rl.clients {
				if time.Since(limiter.lastSeen) > time.Hour {
					delete(rl.clients, ip)
				}
			}
			rl.mu.Unlock()
		}
	}()

	return rl
}

var globalRateLimiter *RateLimiter
var globalLimiterOnce sync.Once

func getGlobalRateLimiter() *RateLimiter {
	globalLimiterOnce.Do(func() {
		globalRateLimiter = NewRateLimiter(defaultRateLimitRPM, defaultRateLimitEnabled)
	})
	return globalRateLimiter
}

// NewRateLimiterMiddleware creates a new HTTP middleware that enforces per-client IP rate limits
func NewRateLimiterMiddleware(rps float64, burst int) func(http.Handler) http.Handler {
	rl := &RateLimiter{
		rps:     rate.Limit(rps),
		burst:   burst,
		clients: make(map[string]*clientLimiter),
		enabled: true,
	}

	// Start background goroutine to clean up old client entries
	go func() {
		for {
			time.Sleep(time.Hour)
			rl.mu.Lock()
			for ip, limiter := range rl.clients {
				if time.Since(limiter.lastSeen) > time.Hour {
					delete(rl.clients, ip)
				}
			}
			rl.mu.Unlock()
		}
	}()

	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			// Only apply rate limiting to public checkout API endpoints
			if !strings.HasPrefix(r.URL.Path, "/api/checkout/") && r.URL.Path != "/api/checkout" {
				next.ServeHTTP(w, r)
				return
			}

			clientIP := getClientIP(r)
			limiter := rl.getLimiter(clientIP)

			if !limiter.Allow() {
				// Calculate retry after time: time until next token is available, rounded up to nearest second
				reservation := limiter.Reserve()
				retryAfter := reservation.Delay().Round(time.Second)
				reservation.Cancel() // Cancel reservation since we're rejecting the request
				if retryAfter < time.Second {
					retryAfter = time.Second
				}
				retryAfterSeconds := int(retryAfter.Seconds())

				// Set response headers
				w.Header().Set("Retry-After", strconv.Itoa(retryAfterSeconds))
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusTooManyRequests)

				// Write response body
				resp := map[string]interface{}{
					"error":       "Too many requests",
					"message":     "Rate limit exceeded, please try again later",
					"retry_after": retryAfterSeconds,
				}
				json.NewEncoder(w).Encode(resp)
				return
			}

			// Pass through to next handler
			next.ServeHTTP(w, r)
		})
	}
}

// getClientIP extracts the client IP from X-Forwarded-For header or RemoteAddr for HTTP requests
func getClientIP(r *http.Request) string {
	xff := r.Header.Get("X-Forwarded-For")
	if xff != "" {
		ips := strings.Split(xff, ",")
		if len(ips) > 0 {
			return strings.TrimSpace(ips[0])
		}
	}

	ip, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return r.RemoteAddr
	}
	return ip
}

// getClientIPFromGRPC extracts the client IP from X-Forwarded-For metadata or peer address for gRPC requests
func getClientIPFromGRPC(ctx context.Context) string {
	// Check for X-Forwarded-For header in metadata
	if md, ok := metadata.FromIncomingContext(ctx); ok {
		if xff := md.Get("x-forwarded-for"); len(xff) > 0 {
			ips := strings.Split(xff[0], ",")
			if len(ips) > 0 {
				return strings.TrimSpace(ips[0])
			}
		}
	}

	// Fall back to peer address
	if p, ok := peer.FromContext(ctx); ok {
		if addr, ok := p.Addr.(*net.TCPAddr); ok {
			return addr.IP.String()
		}
		// For non-TCP addresses, just return the string representation
		return p.Addr.String()
	}

	return "unknown"
}

// getLimiter returns the rate limiter for the given client IP, creating a new one if needed
func (rl *RateLimiter) getLimiter(ip string) *rate.Limiter {
	rl.mu.RLock()
	limiter, exists := rl.clients[ip]
	rl.mu.RUnlock()

	if !exists {
		lim := rate.NewLimiter(rl.rps, rl.burst)
		rl.mu.Lock()
		rl.clients[ip] = &clientLimiter{
			limiter:  lim,
			lastSeen: time.Now(),
		}
		rl.mu.Unlock()
		return lim
	}

	rl.mu.Lock()
	limiter.lastSeen = time.Now()
	rl.mu.Unlock()

	return limiter.limiter
}

// RateLimitUnaryInterceptor returns a gRPC unary server interceptor that enforces per-client rate limits
func RateLimitUnaryInterceptor() grpc.UnaryServerInterceptor {
	rl := getGlobalRateLimiter()
	return func(ctx context.Context, req interface{}, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (interface{}, error) {
		if !rl.enabled {
			return handler(ctx, req)
		}

		clientIP := getClientIPFromGRPC(ctx)
		endpoint := info.FullMethod
		limiter := rl.getLimiter(clientIP)

		if !limiter.Allow() {
			rateLimitExceededTotal.WithLabelValues(clientIP, endpoint).Inc()
			return nil, status.Errorf(codes.ResourceExhausted, "Rate limit exceeded: too many requests from client IP %s, try again later", clientIP)
		}

		rateLimitAllowedTotal.WithLabelValues(clientIP, endpoint).Inc()
		return handler(ctx, req)
	}
}

// RateLimitStreamInterceptor returns a gRPC stream server interceptor that enforces per-client rate limits
func RateLimitStreamInterceptor() grpc.StreamServerInterceptor {
	rl := getGlobalRateLimiter()
	return func(srv interface{}, ss grpc.ServerStream, info *grpc.StreamServerInfo, handler grpc.StreamHandler) error {
		if !rl.enabled {
			return handler(srv, ss)
		}

		clientIP := getClientIPFromGRPC(ss.Context())
		endpoint := info.FullMethod
		limiter := rl.getLimiter(clientIP)

		if !limiter.Allow() {
			rateLimitExceededTotal.WithLabelValues(clientIP, endpoint).Inc()
			return status.Errorf(codes.ResourceExhausted, "Rate limit exceeded: too many requests from client IP %s, try again later", clientIP)
		}

		rateLimitAllowedTotal.WithLabelValues(clientIP, endpoint).Inc()
		return handler(srv, ss)
	}
}
