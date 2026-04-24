#!/bin/bash
# 因为旅行民宿自动客服回复 worker

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="$SCRIPT_DIR/logs/auto_reply_worker_$(date +%Y%m%d).log"
ENV_FILE="$SCRIPT_DIR/travel_automation.env"
LOCAL_ENV_FILE="$SCRIPT_DIR/travel_automation.local.env"
LOCK_DIR="/tmp/travel_automation_auto_reply_worker.lock"
LOCK_PID_FILE="$LOCK_DIR/pid"

mkdir -p "$SCRIPT_DIR/logs"

acquire_lock() {
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        printf '%s\n' "$$" > "$LOCK_PID_FILE"
        return 0
    fi

    existing_pid=""
    if [ -f "$LOCK_PID_FILE" ]; then
        existing_pid="$(cat "$LOCK_PID_FILE" 2>/dev/null || true)"
    fi

    if [ -n "$existing_pid" ] && kill -0 "$existing_pid" 2>/dev/null; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] auto reply worker already running (pid=$existing_pid), skip overlapping run" >> "$LOG_FILE"
        exit 0
    fi

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] removing stale auto reply lock${existing_pid:+ (pid=$existing_pid)}" >> "$LOG_FILE"
    rm -rf "$LOCK_DIR"
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        printf '%s\n' "$$" > "$LOCK_PID_FILE"
        return 0
    fi

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] failed to acquire auto reply lock" >> "$LOG_FILE"
    exit 1
}

cleanup_lock() {
    rm -f "$LOCK_PID_FILE" 2>/dev/null || true
    rmdir "$LOCK_DIR" 2>/dev/null || true
}

acquire_lock

trap cleanup_lock EXIT INT TERM

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

if [ "${TRAVEL_AUTO_REPLY_ENABLED:-1}" != "1" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] auto reply disabled" >> "$LOG_FILE"
    exit 0
fi

ACCOUNTS_RAW="${TRAVEL_AUTO_REPLY_ACCOUNTS:-hotel_1164390341,hotel_95267083}"
IFS=',' read -r -a ACCOUNTS <<< "$ACCOUNTS_RAW"

for account in "${ACCOUNTS[@]}"; do
    account="${account#"${account%%[![:space:]]*}"}"
    account="${account%"${account##*[![:space:]]}"}"
    if [ -z "$account" ]; then
        continue
    fi
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] checking auto reply account: $account" >> "$LOG_FILE"
    /opt/homebrew/bin/python3 "$SCRIPT_DIR/auto_reply.py" "$account" >> "$LOG_FILE" 2>&1 || \
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] auto reply failed for $account" >> "$LOG_FILE"
done
