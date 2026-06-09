package evaluation

import (
	"regexp"

	v1 "github.com/open-telemetry/opentelemetry-demo/src/flagd/evaluation/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

var (
	flagKeyRegex = regexp.MustCompile(`^[a-zA-Z0-9_-]+$`)
	contextValueBlockedChars = regexp.MustCompile(`['";<>\\|&*?#()\x00]`)
)

// ValidateEvaluateRequest validates all fields of an EvaluateRequest before processing
// Returns:
//   - nil if request is valid
//   - gRPC InvalidArgument status error with descriptive message if validation fails
func ValidateEvaluateRequest(req *v1.EvaluateRequest) error {
	// Check required flag key
	if req.FlagKey == "" {
		return status.Error(codes.InvalidArgument, "flag key is required")
	}

	// Check required user ID
	if req.UserId == "" {
		return status.Error(codes.InvalidArgument, "user ID is required")
	}

	// Validate flag key format
	if !flagKeyRegex.MatchString(req.FlagKey) {
		return status.Error(codes.InvalidArgument, "flag key must only contain alphanumeric characters, hyphens, and underscores")
	}

	// Validate context attributes
	for key, value := range req.Context {
		if contextValueBlockedChars.MatchString(value) {
			return status.Errorf(codes.InvalidArgument, "context attribute '%s' contains invalid characters", key)
		}
	}

	return nil
}
