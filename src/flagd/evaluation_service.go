package main

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"

	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/metric"
)

type EvaluationService struct {
	metrics *Metrics
	config  *FlagConfig
}

func NewEvaluationService(metrics *Metrics, config *FlagConfig) *EvaluationService {
	return &EvaluationService{
		metrics: metrics,
		config:  config,
	}
}

type EvaluateRequest struct {
	FlagKey string          `json:"flagKey"`
	Context json.RawMessage `json:"context"`
}

type EvaluateResponse struct {
	Success bool        `json:"success"`
	Value   interface{} `json:"value,omitempty"`
	Error   string      `json:"error,omitempty"`
}

func (s *EvaluationService) EvaluateHandler(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	var req EvaluateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		s.recordError(r.Context(), "invalid_context")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(EvaluateResponse{
			Success: false,
			Error:   "invalid request body",
		})
		return
	}

	// Validate flag key
	req.FlagKey = strings.TrimSpace(req.FlagKey)
	if req.FlagKey == "" {
		s.recordError(r.Context(), "invalid_flag_key")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(EvaluateResponse{
			Success: false,
			Error:   "flag key is required",
		})
		return
	}

	// Check if flag exists
	value, exists := s.config.Flags[req.FlagKey]
	if !exists {
		s.recordError(r.Context(), "flag_not_found")
		w.WriteHeader(http.StatusNotFound)
		json.NewEncoder(w).Encode(EvaluateResponse{
			Success: false,
			Error:   "flag not found",
		})
		return
	}

	// Success response
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(EvaluateResponse{
		Success: true,
		Value:   value,
	})
}

func (s *EvaluationService) recordError(ctx context.Context, errorType string) {
	s.metrics.EvaluationErrorsTotal.Add(ctx, 1, metric.WithAttributes(
		attribute.String("error_type", errorType),
	))
}
