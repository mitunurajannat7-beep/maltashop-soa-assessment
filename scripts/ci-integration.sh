#!/usr/bin/env bash
set -euo pipefail

dc() { docker compose -f compose.yaml -f compose.ci.yaml "$@"; }

dc config --quiet
dc up -d --no-build --wait --wait-timeout 300
dc exec -T order python -m scripts.smoke_test

dc exec -T order python -m scripts.resilience_check restart-prepare
dc restart order
dc up -d --no-build --wait --wait-timeout 300
dc exec -T order python -m scripts.resilience_check restart-check

dc stop broker
dc exec -T order python -m scripts.resilience_check outage-prepare
dc start broker
dc up -d --no-build --wait --wait-timeout 300
dc exec -T order python -m scripts.resilience_check outage-check

printf '%s\n' 'LIVE INTEGRATION AND RESILIENCE CHECKS PASSED'
