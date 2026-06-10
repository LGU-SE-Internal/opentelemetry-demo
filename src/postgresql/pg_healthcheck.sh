#!/bin/bash
set -eo pipefail

# First check if postgres is accepting connections
if ! pg_isready -q; then
  echo "PostgreSQL is not accepting connections"
  exit 1
fi

# Then run a simple query to ensure the database is operational
if ! psql -q -c "SELECT 1" > /dev/null 2>&1; then
  echo "PostgreSQL is accepting connections but failed to execute test query"
  exit 1
fi

# All checks passed
exit 0
