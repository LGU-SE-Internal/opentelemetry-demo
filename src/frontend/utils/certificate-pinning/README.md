# Certificate Pinning Implementation

This module provides certificate public key hash pinning for the OpenTelemetry Demo React Native mobile app to prevent man-in-the-middle attacks.

## Features
- Validates backend service TLS certificates against known public key hashes
- Supports remote config updates without app store submission
- Grace period for expired/rotated certificates to avoid breaking app functionality
- Metrics collection for pin validation success/failure rates
- Integration with axios and fetch network layers

## How to Update Pin Hashes When Backend Certificates Rotate

### Prerequisites
- OpenSSL installed on your local machine
- Access to the backend service's new certificate
- Access to the remote config service (e.g. Firebase Remote Config, LaunchDarkly)

### Step 1: Extract Public Key Hash from New Certificate
1. Get the PEM encoded certificate from the backend service:
   ```bash
   openssl s_client -connect api.example.com:443 -servername api.example.com < /dev/null | openssl x509 -outform PEM > new_cert.pem
   ```

2. Generate SHA256 hash of the public key:
   ```bash
   openssl x509 -in new_cert.pem -pubkey -noout | openssl pkey -pubin -outform der | openssl dgst -sha256 -binary | openssl base64
   ```

3. (Optional) Generate SHA384 hash if using that algorithm:
   ```bash
   openssl x509 -in new_cert.pem -pubkey -noout | openssl pkey -pubin -outform der | openssl dgst -sha384 -binary | openssl base64
   ```

### Step 2: Update the Pin Configuration
1. Add the new pin to your configuration, keeping the old pin for a transition period:
   ```json
   {
     "pins": [
       {
         "domain": "api.example.com",
         "hash": "new_sha256_hash_here",
         "algorithm": "sha256",
         "expiresAt": 1735689600 // New expiration timestamp (unix epoch seconds)
       },
       {
         "domain": "api.example.com",
         "hash": "old_sha256_hash_here",
         "algorithm": "sha256",
         "expiresAt": 1704067200 // Old expiration timestamp
       }
     ],
     "enforcePinning": true,
     "expiredPinGracePeriodDays": 7
   }
   ```

2. Deploy the updated configuration to your remote config service. The app will automatically pick up the new config without requiring a restart.

### Step 3: Verify the Update
1. Check the OpenTelemetry metrics to ensure pin validation success rates remain at 100% after the update.
2. Once the old certificate is fully retired and no longer in use, you can remove the old pin from the configuration.

## Default Configuration
The default pin configuration is defined in the app's environment settings. This is used as a fallback when remote config is unavailable.

## Metrics Export
Pin validation metrics are automatically collected and can be exported to OpenTelemetry using the `getPinValidationMetrics()` function. Metrics include:
- Success/failure counts per domain
- Error type distribution (PIN_MISMATCH, PIN_EXPIRED, NO_PIN_CONFIGURED, CERTIFICATE_ERROR)
- Average validation duration per domain

## Integration with Network Layer
To enable pin validation for all API calls, call either:
- `setupCertificatePinningInterceptor()` for axios
- `setupFetchInterceptor()` for the standard Fetch API

during app initialization, after calling `initializeCertificatePinning()`.
