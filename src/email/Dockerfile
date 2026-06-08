# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


FROM docker.io/library/ruby:3.4.8-alpine3.22@sha256:4e852a14c88d2f30c401eba8b573ed382905719040e3b3079e1dfd2369cba2ff AS builder

COPY ./src/email/Gemfile Gemfile
COPY ./src/email/Gemfile.lock Gemfile.lock

RUN apk update && \
    apk add make gcc musl-dev gcompat && \
    bundle install

FROM docker.io/library/ruby:3.4.8-alpine3.22@sha256:4e852a14c88d2f30c401eba8b573ed382905719040e3b3079e1dfd2369cba2ff

COPY --from=builder /usr/local/bundle/ /usr/local/bundle/

WORKDIR /email_server

COPY ./src/email/views/ views/

COPY ./src/email/.ruby-version .ruby-version
COPY ./src/email/Gemfile Gemfile
COPY ./src/email/Gemfile.lock Gemfile.lock
COPY ./src/email/email_server.rb email_server.rb

# Create non-root user and set permissions
RUN addgroup -S appgroup && adduser -S appuser -G appgroup -u 1000
RUN chown -R appuser:appgroup /email_server
USER appuser

# Optional certificate mount points
VOLUME ["/app/certs/server.crt", "/app/certs/server.key", "/app/certs/ca.crt"]

EXPOSE ${EMAIL_PORT:-8080} 8443
ENTRYPOINT ["bundle", "exec", "ruby", "email_server.rb"]
