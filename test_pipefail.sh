#!/bin/bash
set -euo pipefail
# Test pipefail: false | true should return non-zero with pipefail enabled
if false | true; then
    echo "pipefail NOT working"
    exit 1
else
    echo "pipefail is working correctly"
    exit 0
fi
