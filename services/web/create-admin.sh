#!/usr/bin/env bash
# Compatibility entry point to create a NEW administrator. Use mykos-users passwd to reset.
set -euo pipefail
name=${1:-admin}
exec /usr/local/sbin/mykos-users create "$name" --role admin
