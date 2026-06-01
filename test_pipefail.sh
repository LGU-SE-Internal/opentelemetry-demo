#!/bin/bash
set -euo pipefail

# Test pipeline failure
false | true
echo "If we see this, pipefail is NOT working. Exit code: $?"
