# Certificate Pinning Configuration Guide

This document describes how to update certificate public key pins when backend services are rotated or renewed.

## Prerequisites
- OpenSSL installed on your local machine
- Access to the new X509 certificate file (.crt, .pem, .cer) for the backend service
- Access to the remote config service (e.g. Firebase Remote Config, LaunchDarkly) used by the mobile app

## Step 1: Extract Public Key from Certificate

Run the following command to extract the public key from your new certificate file and compute its SHA256 hash:

```bash
openssl x509 -in your-certificate.crt -pubkey -noout | \
  openssl pkey -pubin -outform der | \
  openssl dgst -sha256 -binary | \
  openssl enc -base64
```

For SHA384 hash:
```bash
openssl x509 -in your-certificate.crt -pubkey -noout | \
  openssl pkey -pubin -outform der | \
  openssl dgst -sha384 -binary | \
  openssl enc -base64
```

The output will be a base64 encoded string like: `abc123def456ghi789jkl012mnop345qrs678tuv90=` — this is your pin hash.

## Step 2: Verify the Hash

To ensure you have the correct hash, you can verify it against the running service directly:
```bash
openssl s_client -connect api.example.com:443 | \
  openssl x509 -pubkey -noout | \
  openssl pkey -pubin -outform der | \
  openssl dgst -sha256 -binary | \
  openssl enc -base64
```

Replace `api.example.com:443` with your actual service domain and port. The output should match the hash you generated in step 1.

## Step 3: Update the Pin Configuration

1. Get the current pin configuration from your remote config service
2. Add the new pin entry to the `pins` array:
   ```json
   {
     "domain": "api.example.com",
     "hash": "abc123def456ghi789jkl012mnop345qrs678tuv90=",
     "algorithm": "sha256",
     "expiresAt": 1735689600
   }
   ```
   - `expiresAt` should be set to the Unix epoch timestamp when the new certificate expires
   - Keep the old pin entry in the config for at least 7 days (or your configured grace period) to ensure all app instances have received the new config before the old certificate is retired

3. If you are retiring an old pin, set its `expiresAt` to the date when the old certificate will be taken out of service

## Step 4: Deploy the Updated Config

1. Save the updated configuration to your remote config service
2. The new configuration will be automatically picked up by all running app instances without requiring an app store submission
3. Monitor the pin validation metrics for any increase in PIN_MISMATCH or PIN_EXPIRED errors

## Rollback Procedure

If you notice issues after deploying the new pin config:
1. Revert the remote config to the previous version
2. Apps will automatically revert to the previous pin set within their config refresh interval
3. No app updates are required for rollback

## Grace Period Configuration

The `expiredPinGracePeriodDays` setting controls how long expired pins will continue to be accepted after their expiration date. We recommend setting this to at least 7 days to allow time for all app instances to receive updated pin configurations before old certificates are retired.

## Important Notes
- Always add new pins *before* deploying the new certificate to production
- Keep multiple valid pins in the config during transition periods
- Use public key hashes, not certificate hashes, to allow for certificate reissuance with the same key pair
- Never remove a pin until after the corresponding certificate is no longer in use by any backend service
