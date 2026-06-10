# OpenSearch Service Configuration

## TLS/mTLS Encryption

The OpenSearch service supports TLS 1.2+ encryption for both HTTP REST API endpoints (port 9200) and inter-node transport endpoints (port 9300), with optional mTLS client certificate authentication.

### Environment Variables

| Variable Name | Type | Default | Description |
|---------------|------|---------|-------------|
| `OPENSEARCH_HTTP_TLS_ENABLED` | boolean | `false` | Toggle TLS encryption for REST API (HTTP) endpoint |
| `OPENSEARCH_TRANSPORT_TLS_ENABLED` | boolean | `false` | Toggle TLS encryption for inter-node transport endpoint |
| `OPENSEARCH_HTTP_MTLS_ENABLED` | boolean | `false` | Require client certificate authentication for HTTP API requests (only applies if `OPENSEARCH_HTTP_TLS_ENABLED=true`) |
| `OPENSEARCH_TRANSPORT_MTLS_ENABLED` | boolean | `true` | Require client certificate authentication for inter-node transport communication (only applies if `OPENSEARCH_TRANSPORT_TLS_ENABLED=true`) |
| `OPENSEARCH_TLS_CERT_PATH` | string | `/usr/share/opensearch/config/certs/tls.crt` | Path to PEM-formatted TLS server certificate |
| `OPENSEARCH_TLS_KEY_PATH` | string | `/usr/share/opensearch/config/certs/tls.key` | Path to PEM-formatted TLS server private key |
| `OPENSEARCH_TLS_CA_PATH` | string | `/usr/share/opensearch/config/certs/ca.crt` | Path to PEM-formatted CA certificate chain for verifying client certificates |

### Kubernetes Deployment

#### Required Secret Structure
Create a Kubernetes secret named `opensearch-tls` with the following keys (PEM format):
- `tls.crt`: Server certificate chain
- `tls.key`: Server private key
- `ca.crt`: CA certificate chain (required for mTLS)

Example secret creation:
```bash
kubectl create secret generic opensearch-tls \
  --from-file=tls.crt=./path/to/server.crt \
  --from-file=tls.key=./path/to/server.key \
  --from-file=ca.crt=./path/to/ca.crt
```

#### Enabling TLS
1. Create the `opensearch-tls` secret as above
2. Set the following environment variables in the StatefulSet:
   ```
   OPENSEARCH_HTTP_TLS_ENABLED=true
   OPENSEARCH_TRANSPORT_TLS_ENABLED=true
   ```
3. (Optional) To enable mTLS for HTTP API access:
   ```
   OPENSEARCH_HTTP_MTLS_ENABLED=true
   ```

### Docker Deployment

To run with TLS enabled in Docker:
1. Mount your PEM certificate files to `/usr/share/opensearch/config/certs/`
2. Set the required environment variables:
   ```bash
   docker run -d \
     -v ./certs:/usr/share/opensearch/config/certs:ro \
     -e OPENSEARCH_HTTP_TLS_ENABLED=true \
     -e OPENSEARCH_TRANSPORT_TLS_ENABLED=true \
     -p 9200:9200 \
     opensearchproject/opensearch:3.5.0
   ```

### Backwards Compatibility
All TLS configuration is disabled by default. Existing deployments without TLS enabled continue to work without any changes required.
