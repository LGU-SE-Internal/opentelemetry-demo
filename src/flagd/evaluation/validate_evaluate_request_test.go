package evaluation

import (
	"testing"

	v1 "github.com/open-telemetry/opentelemetry-demo/src/flagd/evaluation/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"github.com/stretchr/testify/assert"
)

// AC-1: Empty flag_key returns InvalidArgument with "flag key is required"
func Test_AC1_EmptyFlagKey_ReturnsInvalidArgument(t *testing.T) {
	req := &v1.EvaluateRequest{
		FlagKey: "",
		UserId:  "test-user-123",
		Context: map[string]string{"app": "test-app"},
	}

	err := ValidateEvaluateRequest(req)
	assert.Error(t, err)
	assert.Equal(t, codes.InvalidArgument, status.Code(err))
	assert.Equal(t, "flag key is required", status.Convert(err).Message())
}

// AC-2: Empty user_id returns InvalidArgument with "user ID is required"
func Test_AC2_EmptyUserId_ReturnsInvalidArgument(t *testing.T) {
	req := &v1.EvaluateRequest{
		FlagKey: "valid-flag_123",
		UserId:  "",
		Context: map[string]string{"app": "test-app"},
	}

	err := ValidateEvaluateRequest(req)
	assert.Error(t, err)
	assert.Equal(t, codes.InvalidArgument, status.Code(err))
	assert.Equal(t, "user ID is required", status.Convert(err).Message())
}

// AC-3: Invalid flag key characters return InvalidArgument with correct message
func Test_AC3_InvalidFlagKeyCharacters_ReturnsInvalidArgument(t *testing.T) {
	testCases := []struct {
		name    string
		flagKey string
	}{
		{"flag key with space", "test flag 123"},
		{"flag key with special char @", "test@flag"},
		{"flag key with dot", "test.flag"},
		{"flag key with slash", "test/flag"},
		{"flag key with dollar", "test$flag"},
		{"flag key with hash", "test#flag"},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			req := &v1.EvaluateRequest{
				FlagKey: tc.flagKey,
				UserId:  "test-user-123",
				Context: map[string]string{"app": "test-app"},
			}

			err := ValidateEvaluateRequest(req)
			assert.Error(t, err)
			assert.Equal(t, codes.InvalidArgument, status.Code(err))
			assert.Equal(t, "flag key must only contain alphanumeric characters, hyphens, and underscores", status.Convert(err).Message())
		})
	}
}

// AC-4: Valid flag keys pass validation
func Test_AC4_ValidFlagKey_PassesValidation(t *testing.T) {
	testCases := []struct {
		name    string
		flagKey string
	}{
		{"lowercase letters only", "testflag"},
		{"uppercase letters only", "TESTFLAG"},
		{"numbers only", "123456"},
		{"letters and numbers", "testFlag123"},
		{"with hyphen", "test-flag-123"},
		{"with underscore", "test_flag_123"},
		{"mix of all valid", "Test-Flag_123"},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			req := &v1.EvaluateRequest{
				FlagKey: tc.flagKey,
				UserId:  "test-user-123",
				Context: map[string]string{"app": "test-app"},
			}

			err := ValidateEvaluateRequest(req)
			assert.NoError(t, err)
		})
	}
}

// AC-5: Context attribute with injection patterns returns InvalidArgument
func Test_AC5_InvalidContextAttribute_ReturnsInvalidArgument(t *testing.T) {
	testCases := []struct {
		name        string
		contextKey  string
		contextVal  string
	}{
		{"sql injection single quote", "user_input", "admin' OR '1'='1"},
		{"xss script tag", "user_input", "<script>alert('xss')</script>"},
		{"command injection semicolon", "command", "; rm -rf /"},
		{"backslash character", "path", "..\\..\\etc\\passwd"},
		{"pipe character", "input", "test | ls -la"},
		{"ampersand character", "input", "test & echo hacked"},
		{"null character", "input", "test\x00injection"},
		{"parentheses", "input", "test() {}"},
		{"asterisk wildcard", "input", "test*"},
		{"question mark wildcard", "input", "test?"},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			req := &v1.EvaluateRequest{
				FlagKey: "valid-flag_123",
				UserId:  "test-user-123",
				Context: map[string]string{tc.contextKey: tc.contextVal},
			}

			err := ValidateEvaluateRequest(req)
			assert.Error(t, err)
			assert.Equal(t, codes.InvalidArgument, status.Code(err))
			assert.Equal(t, "context attribute '"+tc.contextKey+"' contains invalid characters", status.Convert(err).Message())
		})
	}
}

// AC-7: Valid requests pass validation and proceed normally
func Test_AC7_ValidRequest_PassesAllValidation(t *testing.T) {
	req := &v1.EvaluateRequest{
		FlagKey: "valid-flag_123",
		UserId:  "test-user-123",
		Context: map[string]string{
			"app": "test-app-v1.0.0",
			"environment": "production",
			"email": "user@example.com",
			"path": "/api/v1/users",
			"name": "Test User",
		},
	}

	err := ValidateEvaluateRequest(req)
	assert.NoError(t, err)
}
