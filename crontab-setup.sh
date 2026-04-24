#!/bin/bash
# 定时任务安装脚本
# 运行一次即可

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNNER_JOB="0 18 * * * bash $SCRIPT_DIR/runner.sh >> $SCRIPT_DIR/logs/cron_\$(date +\\%Y\\%m\\%d).log 2>&1"
APPROVAL_JOB="*/5 * * * * bash $SCRIPT_DIR/approval_worker.sh"
AUTO_REPLY_JOB="*/3 * * * * bash $SCRIPT_DIR/auto_reply_worker.sh"

CURRENT_CRONTAB="$(crontab -l 2>/dev/null || true)"

echo "当前定时任务:"
if [ -n "$CURRENT_CRONTAB" ]; then
    printf '%s\n' "$CURRENT_CRONTAB"
else
    echo "（无）"
fi

echo ""
echo "将确保存在以下定时任务:"
echo "$RUNNER_JOB"
echo "$APPROVAL_JOB"
echo "$AUTO_REPLY_JOB"
echo ""

read -p "确认写入？(y/n) " confirm
if [ "$confirm" != "y" ]; then
    echo "已取消"
    exit 0
fi

FILTERED_CRONTAB="$(printf '%s\n' "$CURRENT_CRONTAB" | grep -v "$SCRIPT_DIR/runner.sh" | grep -v "$SCRIPT_DIR/approval_worker.sh" | grep -v "$SCRIPT_DIR/auto_reply_worker.sh" | grep -v "$SCRIPT_DIR/auto_reply.py" || true)"
{
    if [ -n "$FILTERED_CRONTAB" ]; then
        printf '%s\n' "$FILTERED_CRONTAB"
    fi
    echo "$RUNNER_JOB"
    echo "$APPROVAL_JOB"
    echo "$AUTO_REPLY_JOB"
} | crontab -

echo "✅ 定时任务已写入"
echo ""
echo "当前 crontab:"
crontab -l
