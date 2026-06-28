#!/bin/bash
# publish.sh — A股资金流向：采集 + 视频 + 飞书推送
# Usage: ./publish.sh morning | afternoon
set -euo pipefail

SESSION="${1:-morning}"
SCRIPT_DIR="/Users/wangxianshuo/Projects/personal/a-stock-capital-flow"
VENV_PYTHON="$SCRIPT_DIR/venv/bin/python"
DATE_STR=$(date +%Y-%m-%d)

echo "[$(date '+%H:%M:%S')] === Pipeline: $SESSION ==="

# ── Step 1: 采集（阻塞直到午盘/收盘）──
echo "[$(date '+%H:%M:%S')] Starting collector..."
"$VENV_PYTHON" "$SCRIPT_DIR/collector.py" "$SESSION"

# ── Step 2: 生成两个视频（ECharts + Playwright）──
echo "[$(date '+%H:%M:%S')] Generating line chart video..."
"$VENV_PYTHON" "$SCRIPT_DIR/render_video.py" line "$DATE_STR" "$SESSION"
echo "[$(date '+%H:%M:%S')] Generating bar chart video..."
"$VENV_PYTHON" "$SCRIPT_DIR/render_video.py" bar "$DATE_STR" "$SESSION"

# ── 输出视频路径 ──
LABEL_EN="midday"
if [ "$SESSION" = "afternoon" ]; then LABEL_EN="close"; fi
LINE_VIDEO="$SCRIPT_DIR/videos/${DATE_STR}/${DATE_STR}_${LABEL_EN}_line.mp4"
BAR_VIDEO="$SCRIPT_DIR/videos/${DATE_STR}/${DATE_STR}_${LABEL_EN}_bar.mp4"

# ── Step 3: 推送到飞书 ──
echo "[$(date '+%H:%M:%S')] Sending to Feishu..."
if [ ! -f "$LINE_VIDEO" ]; then
    echo "[$(date '+%H:%M:%S')] ERROR: Line video not found: $LINE_VIDEO" >&2
else
    "$VENV_PYTHON" "$SCRIPT_DIR/send_feishu.py" "$LINE_VIDEO" "$SESSION"
fi
if [ ! -f "$BAR_VIDEO" ]; then
    echo "[$(date '+%H:%M:%S')] ERROR: Bar video not found: $BAR_VIDEO" >&2
else
    "$VENV_PYTHON" "$SCRIPT_DIR/send_feishu.py" "$BAR_VIDEO" "$SESSION"
fi
echo "[$(date '+%H:%M:%S')] Done."