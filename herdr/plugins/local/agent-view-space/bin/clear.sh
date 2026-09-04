#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
. ./lib.sh
herdr_api '{"id":"agent-view-space:clear","method":"agent.view.clear","params":{"source":"agent-view-space"}}'
herdr_state_write all
