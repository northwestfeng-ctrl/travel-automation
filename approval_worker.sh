#!/bin/bash
# 因为旅行民宿自动化 - 飞书审批轮询 worker

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="$SCRIPT_DIR/logs/approval_worker_$(date +%Y%m%d).log"
ENV_FILE="$SCRIPT_DIR/travel_automation.env"
LOCAL_ENV_FILE="$SCRIPT_DIR/travel_automation.local.env"

mkdir -p "$SCRIPT_DIR/logs"

if [ -f "$ENV_FILE" ]; then
    set -a
    . "$ENV_FILE"
    set +a
fi

if [ -f "$LOCAL_ENV_FILE" ]; then
    set -a
    . "$LOCAL_ENV_FILE"
    set +a
fi

ARGS=()
if [ "${TRAVEL_APPROVAL_NOTIFY:-1}" = "1" ]; then
    ARGS+=(--notify)
fi
if [ "${TRAVEL_APPROVAL_COMMIT:-1}" = "1" ]; then
    ARGS+=(--commit)
fi
if [ -n "${TRAVEL_APPROVAL_MAX_AGE_HOURS:-}" ]; then
    ARGS+=(--max-age-hours "$TRAVEL_APPROVAL_MAX_AGE_HOURS")
fi
if [ -n "${TRAVEL_APPROVAL_MAX_OPS:-}" ]; then
    ARGS+=(--max-ops "$TRAVEL_APPROVAL_MAX_OPS")
fi
if [ -n "${TRAVEL_APPROVAL_LIMIT:-}" ]; then
    ARGS+=(--limit "$TRAVEL_APPROVAL_LIMIT")
fi

/opt/homebrew/bin/python3 "$SCRIPT_DIR/feishu_approval_worker.py" "${ARGS[@]}" >> "$LOG_FILE" 2>&1
