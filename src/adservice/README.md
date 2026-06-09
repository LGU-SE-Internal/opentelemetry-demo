# Ad Service

The Ad Service is responsible for serving ad recommendations to the frontend. It is a gRPC service written in Go.

## Configuration

The service is configured via the following environment variables:

| Variable Name | Type | Default Value | Required | Description |
|---------------|------|---------------|----------|-------------|
| `AD_SERVICE_PORT` | integer | `9555` | No | TCP port the service listens on for incoming requests |
| `AD_DB_HOST` | string | `localhost` | No | Database server hostname/IP |
| `AD_DB_PORT` | integer | `5432` | No | Database server port |
| `AD_DB_USER` | string | `postgres` | No | Database authentication username |
| `AD_DB_PASSWORD` | string | `postgres` | No | Database authentication password |
| `AD_DB_NAME` | string | `ads` | No | Database name to connect to |

## Database Requirements

The service uses a PostgreSQL database to store ad data. The database schema should be initialized with the appropriate tables before starting the service.

## Running the Service

```bash
# Build the service
go build -o adservice .

# Run the service with default configuration
./adservice

# Run the service with custom configuration
AD_SERVICE_PORT=8080 AD_DB_HOST=staging-db.internal ./adservice
```
