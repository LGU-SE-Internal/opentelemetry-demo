FROM postgres:15-bookworm

# Install required packages: openssl for certificate validation
RUN apt-get update && apt-get install -y --no-install-recommends \
    openssl \
    && rm -rf /var/lib/apt/lists/*

# Create TLS directories with correct permissions
RUN mkdir -p /etc/postgresql/tls/certs /etc/postgresql/tls/private \
    && chown -R postgres:postgres /etc/postgresql/tls \
    && chmod 755 /etc/postgresql/tls/certs \
    && chmod 700 /etc/postgresql/tls/private

# Declare volumes for TLS certificates and keys
VOLUME ["/etc/postgresql/tls/certs", "/etc/postgresql/tls/private"]

# Copy custom entrypoint, init script, and health check script
COPY entrypoint.sh /usr/local/bin/
COPY init.sql /docker-entrypoint-initdb.d/
COPY pg_healthcheck.sh /usr/local/bin/

# Make entrypoint and health check script executable
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/pg_healthcheck.sh

# Set custom entrypoint
ENTRYPOINT ["entrypoint.sh"]

# Default command to run postgres
CMD ["postgres"]

# Health check configuration
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 CMD /usr/local/bin/pg_healthcheck.sh
