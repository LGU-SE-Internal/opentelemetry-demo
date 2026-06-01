#!/bin/bash
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

set -e # Exit immediately if a command exits with a non-zero status.
set -x # Print commands and their arguments as they are executed
set -o pipefail # Fail pipeline if any command in the pipeline fails

# This script is used to generate protobuf files for all services with Docker.

. ./.env

gen_proto_go() {
  echo "Generating Go protobuf files for $1"
  docker build -f "src/$1/genproto/Dockerfile" -t "$1-genproto" .
  docker run --rm -v $(pwd):/build "$1-genproto" \
    protoc -I /build/pb /build/pb/demo.proto --go_out="./src/$1/" --go-grpc_out="./src/$1/"
}

gen_proto_cpp() {
  echo "Generating Cpp protobuf files for $1"
  docker build --build-arg OPENTELEMETRY_CPP_VERSION=${OPENTELEMETRY_CPP_VERSION} -f "src/$1/genproto/Dockerfile" -t "$1-genproto" .
  docker run --rm -v $(pwd):/build "$1-genproto" \
    cp -r "/$1/build/generated" "/build/src/$1/build/"
}

gen_proto_python() {
  echo "Generating Python protobuf files for $1"
  docker build -f "src/$1/genproto/Dockerfile" -t "$1-genproto" .
  docker run --rm -v $(pwd):/build "$1-genproto" \
    python -m grpc_tools.protoc -I /build/pb/ --python_out="./src/$1/" --grpc_python_out="./src/$1/" /build/pb/demo.proto
}

gen_proto_ts() {
  echo "Generating Typescript protobuf files for $1"
  docker build -f "src/$1/genproto/Dockerfile" -t "$1-genproto" .
  docker run --rm -e SERVICE=$1 -v $(pwd):/build "$1-genproto" /bin/sh -c '
    mkdir -p /build/src/$SERVICE/protos && \
    protoc -I /build/pb \
    --plugin=protoc-gen-ts_proto=/app/node_modules/.bin/protoc-gen-ts_proto \
    --ts_proto_opt=esModuleInterop=true \
    --ts_proto_out="/build/src/$SERVICE/protos" \
    --ts_proto_opt=outputServices=grpc-js \
    /build/pb/demo.proto'
}
# Check for help argument
if [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
  cat << EOF
Usage: ./docker-gen-proto.sh [generation-type] [service-name]

Generates protobuf files for all services with Docker.

If no arguments are provided: runs all pre-configured generation jobs for supported services.

Arguments:
  generation-type   Optional type of proto generation to run (supported types: go, cpp, python, ts)
  service-name      Required if generation-type is specified, name of the service to generate proto files for

Examples:
  ./docker-gen-proto.sh                     # Run all pre-configured generation jobs
  ./docker-gen-proto.sh go checkout         # Generate Go protobuf files for the checkout service
  ./docker-gen-proto.sh ts frontend         # Generate TypeScript protobuf files for the frontend service
  ./docker-gen-proto.sh python recommendation # Generate Python protobuf files for the recommendation service
EOF
  exit 0
fi

if [ -z "$1" ]; then
  #gen_proto_dotnet accounting
  #gen_proto_java ad
  #gen_proto_dotnet cart
  gen_proto_go checkout
  gen_proto_cpp currency
  #gen_proto_ruby email
  gen_proto_ts frontend
  #gen_proto_js payment
  gen_proto_go product-catalog
  #gen_proto_php quote
  gen_proto_python product-reviews
  gen_proto_python recommendation
  #gen_proto_rust shipping
else
  GEN_FUNC="gen_proto_$1"
  # Validate that the requested generation function exists
  if ! command -v "$GEN_FUNC" > /dev/null 2>&1; then
    echo "ERROR: Unsupported generation type '$1'. Supported generation types are: go, cpp, python, ts" >&2
    exit 1
  fi

  # Validate that service name is provided
  if [ -z "$2" ]; then
    echo "ERROR: Service name is required when specifying a generation type. Usage: $0 <generation-type> <service-name>" >&2
    exit 1
  fi

  "$GEN_FUNC" "$2"
fi
