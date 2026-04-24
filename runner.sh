#!/bin/bash
# 因为旅行民宿自动化 - 每日定时任务
# 运行时间：每天 18:00

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="$SCRIPT_DIR/logs/cron_$(date +%Y%m%d).log"
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

resolve_date_offset() {
    /opt/homebrew/bin/python3 - "$1" <<'PY'
from datetime import date, timedelta
import sys

offset = int(sys.argv[1])
print((date.today() + timedelta(days=offset)).isoformat())
PY
}

resolve_plan_dates() {
    RESOLVED_PLAN_START_DATE="${TRAVEL_PLAN_START_DATE:-}"
    RESOLVED_PLAN_END_DATE="${TRAVEL_PLAN_END_DATE:-}"

    if [ -n "$RESOLVED_PLAN_START_DATE" ] && [ -n "$RESOLVED_PLAN_END_DATE" ]; then
        return 0
    fi

    if [ -n "${TRAVEL_PLAN_START_OFFSET_DAYS:-}" ]; then
        RESOLVED_PLAN_START_DATE="$(resolve_date_offset "$TRAVEL_PLAN_START_OFFSET_DAYS")"
        local end_offset="${TRAVEL_PLAN_END_OFFSET_DAYS:-$TRAVEL_PLAN_START_OFFSET_DAYS}"
        RESOLVED_PLAN_END_DATE="$(resolve_date_offset "$end_offset")"
    fi
}

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log "========== 开始执行 =========="

# 1. 抓取携程数据
log "Step 1: 抓取携程数据..."
cd "$SCRIPT_DIR/competitor-analysis"
/opt/homebrew/bin/python3 scrape_ctrip.py >> "$LOG_FILE" 2>&1
if [ $? -eq 0 ]; then
    log "✅ 抓取成功"
else
    log "❌ 抓取失败"
    exit 1
fi

# 2. 生成调价建议
log "Step 2: 生成调价建议..."
cd "$SCRIPT_DIR/pricing"
/opt/homebrew/bin/python3 engine.py >> "$LOG_FILE" 2>&1
if [ $? -eq 0 ]; then
    log "✅ 调价建议生成成功"
else
    log "❌ 调价建议生成失败"
    exit 1
fi

# 2.5 可选：生成 ebooking dry-run 执行计划
resolve_plan_dates
if [ -n "${RESOLVED_PLAN_START_DATE:-}" ] && [ -n "${RESOLVED_PLAN_END_DATE:-}" ]; then
    log "Step 2.5: 生成 ebooking dry-run 执行计划..."
    if /opt/homebrew/bin/python3 recommendation_to_execution_plan.py \
        --start-date "$RESOLVED_PLAN_START_DATE" \
        --end-date "$RESOLVED_PLAN_END_DATE" >> "$LOG_FILE" 2>&1; then
        log "✅ ebooking dry-run 执行计划生成成功（$RESOLVED_PLAN_START_DATE -> $RESOLVED_PLAN_END_DATE）"
    else
        log "⚠️ ebooking dry-run 执行计划生成失败（不影响主流程）"
    fi
else
    log "Step 2.5: 未设置计划日期或相对日期偏移，跳过 ebooking dry-run 执行计划生成"
fi

# 3. 推送飞书
log "Step 3: 推送飞书..."
cd "$SCRIPT_DIR"
if /opt/homebrew/bin/python3 feishu_push.py >> "$LOG_FILE" 2>&1; then
    log "✅ 飞书推送完成"
else
    log "⚠️ 飞书推送失败（不影响主流程）"
fi

# 4 可选：等待飞书审批并执行保存计划
if [ -n "${TRAVEL_APPROVAL_WATCH_SECONDS:-}" ]; then
    log "Step 4: 轮询飞书审批..."
    APPROVAL_CMD=(/opt/homebrew/bin/python3 feishu_approval.py --watch-seconds "$TRAVEL_APPROVAL_WATCH_SECONDS")
    if [ "${TRAVEL_APPROVAL_NOTIFY:-0}" = "1" ]; then
        APPROVAL_CMD+=(--notify)
    fi
    if [ "${TRAVEL_APPROVAL_COMMIT:-0}" = "1" ]; then
        APPROVAL_CMD+=(--commit)
    fi
    "${APPROVAL_CMD[@]}" >> "$LOG_FILE" 2>&1 || log "⚠️ 飞书审批轮询失败（不影响主流程）"
else
    log "Step 4: 未设置 TRAVEL_APPROVAL_WATCH_SECONDS，跳过飞书审批轮询"
fi

log "========== 执行完成 =========="
