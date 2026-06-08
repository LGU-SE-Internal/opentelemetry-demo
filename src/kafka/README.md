# Kafka Service with TLS/mTLS Support

This Kafka service is based on the official Apache Kafka image with added support for TLS encryption and mTLS client authentication.

## Configuration Options

### Environment Variables

| Variable Name | Type | Default Value | Description |
|---------------|------|---------------|-------------|
| `KAFKA_TLS_ENABLED` | boolean | `false` | If set to `true`, enables TLS encryption for broker listener endpoints. |
| `KAFKA_MTLS_ENABLED` | boolean | `false` | If set to `true` AND `KAFKA_TLS_ENABLED=true`, enforces mTLS client authentication for all client connections. |
| `KAFKA_KEYSTORE_PATH` | string | `/etc/kafka/secrets/kafka.keystore.jks` | Filesystem path to the JKS keystore containing the broker's TLS certificate and private key. Required if `KAFKA_TLS_ENABLED=true`. |
| `KAFKA_KEYSTORE_PASSWORD` | string | (empty) | Password for the JKS keystore. Required if `KAFKA_TLS_ENABLED=true`. |
| `KAFKA_TRUSTSTORE_PATH` | string | `/etc/kafka/secrets/kafka.truststore.jks` | Filesystem path to the JKS truststore containing trusted CA certificates for client authentication. Required if `KAFKA_MTLS_ENABLED=true`. |
| `KAFKA_TRUSTSTORE_PASSWORD` | string | (empty) | Password for the JKS truststore. Required if `KAFKA_MTLS_ENABLED=true`. |

## Listener Configuration

- When `KAFKA_TLS_ENABLED=false` (default): Broker exposes only `PLAINTEXT://0.0.0.0:9092` listener
- When `KAFKA_TLS_ENABLED=true` and `KAFKA_MTLS_ENABLED=false`: Broker exposes `SSL://0.0.0.0:9093` listener with TLS 1.2+ encryption, no client authentication
- When `KAFKA_TLS_ENABLED=true` and `KAFKA_MTLS_ENABLED=true`: Broker exposes `SSL://0.0.0.0:9093` listener with TLS 1.2+ encryption and required client certificate authentication

## Usage Examples

### Default Plaintext Mode (no TLS)
No additional configuration needed. The broker will listen on port 9092 for plaintext connections.

### TLS Enabled (no mTLS)
```yaml
environment:
  KAFKA_TLS_ENABLED: "true"
  KAFKA_KEYSTORE_PATH: "/path/to/keystore.jks"
  KAFKA_KEYSTORE_PASSWORD: "your-keystore-password"
volumes:
  - ./secrets/kafka.keystore.jks:/path/to/keystore.jks
```

### mTLS Enabled
```yaml
environment:
  KAFKA_TLS_ENABLED: "true"
  KAFKA_MTLS_ENABLED: "true"
  KAFKA_KEYSTORE_PATH: "/path/to/keystore.jks"
  KAFKA_KEYSTORE_PASSWORD: "your-keystore-password"
  KAFKA_TRUSTSTORE_PATH: "/path/to/truststore.jks"
  KAFKA_TRUSTSTORE_PASSWORD: "your-truststore-password"
volumes:
  - ./secrets/kafka.keystore.jks:/path/to/keystore.jks
  - ./secrets/kafka.truststore.jks:/path/to/truststore.jks
```

## Security Notes
- TLS 1.0 and 1.1 are explicitly disabled to align with modern security best practices
- You are responsible for generating and providing valid keystore/truststore files via volumes or secret managers
- Certificate generation and provisioning is out of scope for this service
