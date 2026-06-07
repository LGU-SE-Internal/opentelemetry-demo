package main

import (
	"encoding/json"
	"net"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"golang.org/x/time/rate"
)

type clientLimiter struct {
	limiter  *rate.Limiter
	lastSeen time.Time
}

type RateLimiter struct {
	rps     rate.Limit
	burst   int
	clients map[string]*clientLimiter
	mu      sync.RWMutex
}

// NewRateLimiterMiddleware creates a new HTTP middleware that enforces per-client IP rate limits
func NewRateLimiterMiddleware(rps float64, burst int) func(http.Handler) http.Handler {
	rl := &RateLimiter{
		rps:     rate.Limit(rps),
		burst:   burst,
		clients: make(map[string]*clientLimiter),
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

// getClientIP extracts the client IP from X-Forwarded-For header or RemoteAddr
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

