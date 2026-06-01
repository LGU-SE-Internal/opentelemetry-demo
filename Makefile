# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


# All documents to be used in spell check.
ALL_DOCS := $(shell find . -type f -name '*.md' -not -path './.github/*' -not -path '*/node_modules/*' -not -path '*/_build/*' -not -path '*/deps/*' -not -path */Pods/* -not -path */.expo/* | sort)
PWD := $(shell pwd)

TOOLS_DIR := ./internal/tools
MISSPELL_BINARY=bin/misspell
MISSPELL = $(TOOLS_DIR)/$(MISSPELL_BINARY)
ADDLICENSE_BINARY=bin/addlicense
ADDLICENSE = $(TOOLS_DIR)/$(ADDLICENSE_BINARY)

DOCKER_CMD ?= docker
DOCKER_COMPOSE_CMD ?= docker compose
DOCKER_COMPOSE_ENV=--env-file .env --env-file .env.override

# Compose file layers — combine with -f flags for the desired configuration:
#   Core (minimal):             compose.yaml
#   Full (adds Kafka group):    compose.yaml + compose.full.yaml
#   With observability stack:   + compose.observability.yaml
#   With extras customizations: + compose.extras.yaml (always last)
DOCKER_COMPOSE_FILES_CORE=-f compose.yaml
DOCKER_COMPOSE_FILES_FULL=$(DOCKER_COMPOSE_FILES_CORE) -f compose.full.yaml
DOCKER_COMPOSE_FILES_OBSERVABILITY=-f compose.observability.yaml
DOCKER_COMPOSE_FILES_PROFILING=-f compose.profiling.yaml
DOCKER_COMPOSE_FILES_EXTRAS=-f compose.extras.yaml
DOCKER_COMPOSE_FILES_TESTS=-f compose.tests.yaml

# Default: full demo + observability stack + extras stub
DOCKER_COMPOSE_FILES=$(DOCKER_COMPOSE_FILES_FULL) $(DOCKER_COMPOSE_FILES_OBSERVABILITY) $(DOCKER_COMPOSE_FILES_EXTRAS)

# Accept either `service=` or `SERVICE=` for single-service targets (build, restart, redeploy).
# Must be evaluated at file scope; an `ifdef SERVICE` block inside a recipe is a shell command,
# not a Make conditional, so the alias never takes effect there.
ifdef SERVICE
service := $(SERVICE)
endif


# see https://github.com/open-telemetry/build-tools/releases for semconvgen updates
# Keep links in semantic_conventions/README.md and .vscode/settings.json in sync!
SEMCONVGEN_VERSION=0.11.0
YAMLLINT_VERSION=1.30.0
HADOLINT_VERSION=2.12.0

.PHONY: all
all: install-tools markdownlint misspell yamllint checklicense

$(MISSPELL):
	cd $(TOOLS_DIR) && go build -o $(MISSPELL_BINARY) github.com/client9/misspell/cmd/misspell

$(ADDLICENSE):
	cd $(TOOLS_DIR) && go build -o $(ADDLICENSE_BINARY) github.com/google/addlicense

.PHONY: misspell
misspell:	$(MISSPELL)
	$(MISSPELL) -error $(ALL_DOCS)

.PHONY: misspell-correction
misspell-correction:	$(MISSPELL)
	$(MISSPELL) -w $(ALL_DOCS)

.PHONY: markdownlint
markdownlint:
	@if ! npm ls markdownlint; then npm install; fi
	@for f in $(ALL_DOCS); do \
		echo $$f; \
		npx --no -p markdownlint-cli markdownlint -c .markdownlint.yaml $$f \
			|| exit 1; \
	done

.PHONY: install-yamllint
install-yamllint:
    # Using a venv is recommended
	yamllint --version >/dev/null 2>&1 || pip install -U yamllint~=$(YAMLLINT_VERSION)

.PHONY: yamllint
yamllint: install-yamllint
	yamllint .

.PHONY: install-hadolint
install-hadolint:
	hadolint --version >/dev/null 2>&1 || (wget -O /usr/local/bin/hadolint https://github.com/hadolint/hadolint/releases/download/v$(HADOLINT_VERSION)/hadolint-Linux-x86_64 && chmod +x /usr/local/bin/hadolint)

.PHONY: hadolint
hadolint: install-hadolint ## Run hadolint static analysis on all Dockerfile files to catch best practice violations and security issues
	@echo "Running hadolint on all Dockerfiles..."
	@find . -type f -name 'Dockerfile*' \
		-not -path './.git/*' \
		-not -path '*/node_modules/*' \
		-not -path '*/_build/*' \
		-not -path '*/deps/*' \
		-not -path '*/Pods/*' \
		-not -path '*/.expo/*' \
		-not -path '*/vendor/*' \
		-not -path '*/.venv/*' \
		-not -path '*/dist/*' \
		-not -path '*/build/*' \
		-exec hadolint {} +

.PHONY: checklicense
checklicense:	$(ADDLICENSE)
	@echo "Checking license headers..."
	$(ADDLICENSE) -check -c "The OpenTelemetry Authors" -l apache -s=only -y "" \
		-ignore node_modules/** \
		-ignore .expo/** \
		-ignore Pods/** \
		-ignore **/extras/** \
		-ignore **/vendor/** \
		-ignore **/.venv/** \
		-ignore **/dist/** \
		-ignore **/build/** \
		-ignore **/*_pb2.py \
		-ignore **/*_pb2_grpc.py \
		-ignore **/genproto/** \
		-ignore **/protos/*.ts \
		.

.PHONY: addlicense
addlicense:	$(ADDLICENSE)
	@echo "Adding license headers..."
	$(ADDLICENSE) -c "The OpenTelemetry Authors" -l apache -s=only -y "" \
		-ignore node_modules/** \
		-ignore .expo/** \
		-ignore Pods/** \
		-ignore **/extras/** \
		-ignore **/vendor/** \
		-ignore **/.venv/** \
		-ignore **/dist/** \
		-ignore **/build/** \
		-ignore **/*_pb2.py \
		-ignore **/*_pb2_grpc.py \
		-ignore **/genproto/** \
		-ignore **/protos/*.ts \
		.

.PHONY: checklinks
checklinks:
	@echo "Checking links..."
	lychee --config .lychee.toml --cache .

# Run all checks in order of speed / likely failure.
.PHONY: check
check: misspell markdownlint checklicense checklinks hadolint
	@echo "All checks complete"

# Attempt to fix issues / regenerate tables.
.PHONY: fix
fix: misspell-correction
	@echo "All autofixes complete"

.PHONY: install-tools
install-tools: $(MISSPELL) $(ADDLICENSE)
	npm install
	@echo "All tools installed"

# Use to build all services, or a single service component
# Example: make build service=frontend
.PHONY: build
build:
ifneq ($(strip $(service)),)
	$(DOCKER_COMPOSE_CMD) $(DOCKER_COMPOSE_ENV) $(DOCKER_COMPOSE_FILES) build $(service)
else
	$(DOCKER_COMPOSE_CMD) $(DOCKER_COMPOSE_ENV) $(DOCKER_COMPOSE_FILES) build
endif

.PHONY: build-and-push
build-and-push:
	$(DOCKER_COMPOSE_CMD) $(DOCKER_COMPOSE_ENV) $(DOCKER_COMPOSE_FILES) build --push

# Create multiplatform builder for buildx
.PHONY: create-multiplatform-builder
create-multiplatform-builder:
	docker buildx create --name otel-demo-builder --bootstrap --use --driver docker-container --config ./buildkitd.toml

# Remove multiplatform builder for buildx
.PHONY: remove-multiplatform-builder
remove-multiplatform-builder:
	docker buildx rm otel-demo-builder

# Build and push multiplatform images (linux/amd64, linux/arm64) using buildx.
# Requires docker with buildx enabled and a multi-platform capable builder in use.
# Docker needs to be configured to use containerd storage for images to be loaded into the local registry.
.PHONY: build-multiplatform
build-multiplatform:
	# Because buildx bake does not support --env-file yet, we need to load it into the environment first.
	set -a; . ./.env.override; set +a && docker buildx bake $(DOCKER_COMPOSE_FILES) --load --set "*.platform=linux/amd64,linux/arm64"

.PHONY: build-multiplatform-and-push
build-multiplatform-and-push:
	# Because buildx bake does not support --env-file yet, we need to load it into the environment first.
	set -a; . ./.env.override; set +a && docker buildx bake $(DOCKER_COMPOSE_FILES) --push --set "*.platform=linux/amd64,linux/arm64"

.PHONY: clean-images
clean-images:
	$(DOCKER_CMD) rmi $(shell $(DOCKER_CMD) images --filter=reference="ghcr.io/open-telemetry/demo:latest-*" -q); \
    if [ $$? -ne 0 ]; \
    then \
    	echo; \
        echo "Failed to removed 1 or more OpenTelemetry Demo images."; \
        echo "Check to ensure the Demo is not running by executing: make stop"; \
        false; \
    fi

.PHONY: run-tests
run-tests:
	$(DOCKER_COMPOSE_CMD) $(DOCKER_COMPOSE_ENV) $(DOCKER_COMPOSE_FILES) $(DOCKER_COMPOSE_FILES_TESTS) run frontendTests
	$(DOCKER_COMPOSE_CMD) $(DOCKER_COMPOSE_ENV) $(DOCKER_COMPOSE_FILES) $(DOCKER_COMPOSE_FILES_TESTS) run traceBasedTests

.PHONY: run-tracetesting
run-tracetesting:
	$(DOCKER_COMPOSE_CMD) $(DOCKER_COMPOSE_ENV) $(DOCKER_COMPOSE_FILES) $(DOCKER_COMPOSE_FILES_TESTS) run traceBasedTests ${SERVICES_TO_TEST}

.PHONY: coverage
coverage:
	@echo "Running all unit tests with coverage enabled..."
	# Run all service unit test containers that produce coverage reports
	$(DOCKER_COMPOSE_CMD) $(DOCKER_COMPOSE_ENV) $(DOCKER_COMPOSE_FILES) $(DOCKER_COMPOSE_FILES_TESTS) run --rm frontendTests
	# Add additional service test runs here as coverage is enabled for them
	@echo "Aggregating coverage reports..."
	mkdir -p ./coverage
	@echo "Generating coverage summary..."
	# Output human-readable summary to console
	@for file in ./coverage/*.out ./coverage/*.xml ./coverage/*.json ./coverage/*.lcov; do \
		if [ -f "$$file" ]; then \
			echo "$$(basename $$file) coverage:"; \
			# Handle go coverage files
			if [[ $$file == *.out ]]; then \
				go tool cover -func=$$file 2>/dev/null | tail -1 || echo "  Summary not available for $$file"; \
			# Handle lcov coverage files (JS/TS, Python, etc.)
			elif [[ $$file == *.lcov ]]; then \
				if command -v lcov >/dev/null 2>&1; then \
					lcov --summary $$file 2>/dev/null | grep -E '(Total|lines|functions|branches)' || echo "  Summary not available for $$file"; \
				else \
					echo "  lcov not installed, cannot show summary for $$file"; \
				fi; \
			else \
				echo "  Format not supported for automatic summary"; \
			fi; \
		fi; \
	done
	@echo ""
	@echo "Coverage summary complete"
	# Generate HTML coverage report if HTML=1 is specified
ifdef HTML
	@echo ""
	@echo "Generating HTML coverage report in ./coverage/html..."
	mkdir -p ./coverage/html
	# Handle go coverage HTML
	if [ -f ./coverage/merged.out ]; then \
		go tool cover -html=./coverage/merged.out -o ./coverage/html/index.html; \
	fi
	# Handle lcov HTML
	if [ -f ./coverage/merged.lcov ]; then \
		if command -v genhtml >/dev/null 2>&1; then \
			genhtml -o ./coverage/html ./coverage/merged.lcov >/dev/null 2>&1; \
		else \
			echo "genhtml not installed, cannot generate HTML report for lcov files"; \
		fi; \
	fi
	@echo "HTML coverage report available at ./coverage/html/index.html"
endif

.PHONY: generate-protobuf
generate-protobuf:
	./ide-gen-proto.sh

.PHONY: docker-generate-protobuf
docker-generate-protobuf:
	./docker-gen-proto.sh

.PHONY: clean
clean:
	rm -rf ./src/{checkout,product-catalog}/genproto/oteldemo/
	rm -rf ./src/recommendation/{demo_pb2,demo_pb2_grpc}.py
	rm -rf ./src/frontend/protos/demo.ts

.PHONY: check-clean-work-tree
check-clean-work-tree:
	@if ! git diff --quiet; then \
	  echo; \
	  echo 'Working tree is not clean, did you forget to run "make docker-generate-protobuf"?'; \
	  echo; \
	  git status; \
	  exit 1; \
	fi

.PHONY: start
start:
	$(DOCKER_COMPOSE_CMD) $(DOCKER_COMPOSE_ENV) $(DOCKER_COMPOSE_FILES) up --force-recreate --remove-orphans --detach
	@echo ""
	@echo "OpenTelemetry Demo is running."
	@echo "Go to http://localhost:8080 for the demo UI."
	@echo "Go to http://localhost:8080/jaeger/ui for the Jaeger UI."
	@echo "Go to http://localhost:8080/grafana/ for the Grafana UI."
	@echo "Go to http://localhost:8080/loadgen/ for the Load Generator UI."
	@echo "Go to http://localhost:8080/feature/ to change feature flags."
	@echo "Go to http://localhost:8080/telemetry/ for the Weaver generated telemetry documentation."

.PHONY: start-minimal
start-minimal:
	$(DOCKER_COMPOSE_CMD) $(DOCKER_COMPOSE_ENV) $(DOCKER_COMPOSE_FILES_CORE) $(DOCKER_COMPOSE_FILES_OBSERVABILITY) $(DOCKER_COMPOSE_FILES_EXTRAS) up --force-recreate --remove-orphans --detach
	@echo ""
	@echo "OpenTelemetry Demo is running in minimal mode."
	@echo "Go to http://localhost:8080 for the demo UI."
	@echo "Go to http://localhost:8080/jaeger/ui for the Jaeger UI."
	@echo "Go to http://localhost:8080/grafana/ for the Grafana UI."
	@echo "Go to http://localhost:8080/loadgen/ for the Load Generator UI."
	@echo "Go to http://localhost:8080/feature/ to change feature flags."
	@echo "Go to http://localhost:8080/telemetry/ for the Weaver generated telemetry documentation."

.PHONY: help
help: ## Show this help message
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""
	@echo "Available targets:"
	@echo "\033[36mhadolint\033[0m             Run hadolint static analysis on all Dockerfile files"
	@echo "\033[36mcheck\033[0m                Run all code quality checks (including hadolint)"
	@echo "\033[36mmisspell\033[0m             Check spelling in documentation files"
	@echo "\033[36mmarkdownlint\033[0m         Lint markdown documentation files"
	@echo "\033[36myamllint\033[0m             Lint YAML configuration files"
	@echo "\033[36mchecklicense\033[0m         Check for required license headers in source files"
	@echo "\033[36mchecklinks\033[0m           Check for broken links in documentation"
