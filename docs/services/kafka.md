# Kafka Service Configuration

## Overview
The OpenTelemetry Demo uses Apache Kafka for event streaming between services. This documentation covers how to configure TLS encryption and mTLS client authentication for Kafka in production deployments.

## Default Configuration
By default, Kafka runs with PLAINTEXT listeners only for internal service communication. This configuration is suitable for development and testing environments but is not secure for production use.

## TLS/mTLS Configuration
To enable secure encrypted communication and client authentication for the external Kafka listener (port 9093), you can configure the following environment variables:

| Variable Name | Type | Default | Description |
|---------------|------|---------|-------------|
| `KAFKA_TLS_ENABLED` | boolean | `false` | Toggle to enable TLS support for Kafka external listeners |
| `KAFKA_MTLS_ENABLED` | boolean | `false` | Toggle to enable mTLS client authentication (requires `KAFKA_TLS_ENABLED=true`) |
| `KAFKA_TLS_KEYSTORE_PATH` | string | "" | Filesystem path to the PKCS12 keystore containing the Kafka broker's TLS certificate and private key |
| `KAFKA_TLS_KEYSTORE_PASSWORD` | string | "" | Password for the TLS keystore |
| `KAFKA_TLS_TRUSTSTORE_PATH` | string | "" | Filesystem path to the PKCS12 truststore containing trusted CA certificates for client authentication (required for mTLS) |
| `KAFKA_TLS_TRUSTSTORE_PASSWORD` | string | "" | Password for the TLS truststore |
| `KAFKA_TLS_CLIENT_AUTH` | string | "required" | Client authentication mode when mTLS is enabled: allowed values `required`, `requested`, `none` |

## Certificate Requirements
Only PKCS12 format keystores and truststores are supported. You will need:
1. A PKCS12 keystore for the Kafka broker containing its TLS certificate and private key
2. (Optional for mTLS) A PKCS12 truststore containing the CA certificates used to sign client certificates

### Generating Test Certificates (for testing only)
You can generate self-signed test certificates using the Java `keytool` command:

```bash
# Generate broker keystore
keytool -genkeypair -alias kafka-broker -keyalg RSA -keysize 2048 -storetype PKCS12 \
  -keystore kafka-broker-keystore.p12 -validity 365 -storepass changeit \
  -dname "CN=kafka,OU=Demo,O=OpenTelemetry,L=Unknown,ST=Unknown,C=US" \
  -ext "SAN=DNS:kafka,IP:127.0.0.1"

# Generate CA certificate for client authentication
keytool -genkeypair -alias ca -keyalg RSA -keysize 2048 -storetype PKCS12 \
  -keystore ca-keystore.p12 -validity 365 -storepass changeit \
  -dname "CN=Demo CA,OU=Demo,O=OpenTelemetry,L=Unknown,ST=Unknown,C=US"

# Export CA certificate
keytool -exportcert -alias ca -file ca.crt -keystore ca-keystore.p12 -storepass changeit

# Import CA certificate into broker truststore
keytool -importcert -alias ca -file ca.crt -keystore kafka-broker-truststore.p12 \
  -storepass changeit -noprompt

# Generate client keystore signed by CA
keytool -genkeypair -alias kafka-client -keyalg RSA -keysize 2048 -storetype PKCS12 \
  -keystore kafka-client-keystore.p12 -validity 365 -storepass changeit \
  -dname "CN=kafka-client,OU=Demo,O=OpenTelemetry,L=Unknown,ST=Unknown,C=US"

# Generate client CSR
keytool -certreq -alias kafka-client -file kafka-client.csr -keystore kafka-client-keystore.p12 -storepass changeit

# Sign client CSR with CA
keytool -gencert -alias ca -infile kafka-client.csr -outfile kafka-client.crt \
  -keystore ca-keystore.p12 -storepass changeit -validity 365

# Import CA cert and signed client cert into client keystore
keytool -importcert -alias ca -file ca.crt -keystore kafka-client-keystore.p12 -storepass changeit -noprompt
keytool -importcert -alias kafka-client -file kafka-client.crt -keystore kafka-client-keystore.p12 -storepass changeit
```

## Example Docker Compose Configuration
```yaml
services:
  kafka:
    image: otel/demo-kafka:latest
    environment:
      KAFKA_TLS_ENABLED: "true"
      KAFKA_MTLS_ENABLED: "true"
      KAFKA_TLS_KEYSTORE_PATH: "/etc/kafka/secrets/kafka-broker-keystore.p12"
      KAFKA_TLS_KEYSTORE_PASSWORD: "changeit"
      KAFKA_TLS_TRUSTSTORE_PATH: "/etc/kafka/secrets/kafka-broker-truststore.p12"
      KAFKA_TLS_TRUSTSTORE_PASSWORD: "changeit"
      KAFKA_TLS_CLIENT_AUTH: "required"
    volumes:
      - ./secrets:/etc/kafka/secrets:ro
    ports:
      - "9092:9092" # PLAINTEXT internal listener
      - "9093:9093" # SSL external listener
```

## Example Kubernetes Configuration
```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: kafka
spec:
  template:
    spec:
      containers:
      - name: kafka
        image: otel/demo-kafka:latest
        env:
        - name: KAFKA_TLS_ENABLED
          value: "true"
        - name: KAFKA_MTLS_ENABLED
          value: "true"
        - name: KAFKA_TLS_KEYSTORE_PATH
          value: "/etc/kafka/secrets/kafka-broker-keystore.p12"
        - name: KAFKA_TLS_KEYSTORE_PASSWORD
          valueFrom:
            secretKeyRef:
              name: kafka-secrets
              key: keystore-password
        - name: KAFKA_TLS_TRUSTSTORE_PATH
          value: "/etc/kafka/secrets/kafka-broker-truststore.p12"
        - name: KAFKA_TLS_TRUSTSTORE_PASSWORD
          valueFrom:
            secretKeyRef:
              name: kafka-secrets
              key: truststore-password
        volumeMounts:
        - name: kafka-secrets
          mountPath: /etc/kafka/secrets
          readOnly: true
      volumes:
      - name: kafka-secrets
        secret:
          secretName: kafka-tls-secrets
```

## Listener Ports
- **9092**: PLAINTEXT internal listener (always available, for internal service communication)
- **9093**: SSL external listener (available only when `KAFKA_TLS_ENABLED=true`, for external clients)
- **9094**: Controller listener (internal use only)

## Notes
- Internal service communication over port 9092 remains PLAINTEXT by default to avoid performance overhead
- TLS configuration only applies to the external listener on port 9093
- Automatic certificate generation and renewal are not included - you must provide valid certificates
- JKS format keystores are not supported, only PKCS12 format
