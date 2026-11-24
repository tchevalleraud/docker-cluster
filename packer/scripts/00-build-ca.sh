#!/usr/bin/env bash
# Trust an extra CA during the build only (for builds behind a TLS-inspecting
# proxy). 90-cleanup.sh removes it from the final image.
set -euo pipefail
if [ -n "${BUILD_CA_CERT:-}" ]; then
  printf '%s\n' "$BUILD_CA_CERT" > /usr/local/share/ca-certificates/dc-build-ca.crt
  update-ca-certificates
fi
