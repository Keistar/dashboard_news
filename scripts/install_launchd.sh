#!/bin/bash
# 毎日の更新（scripts/run_daily.sh）を launchd に登録する。この clone を自動実行専用にして、ここから動かす。
#   登録:   scripts/install_launchd.sh
#   外す:   scripts/install_launchd.sh --uninstall
#   今すぐ: launchctl kickstart gui/$(id -u)/com.keistar.dashboard-news
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL=com.keistar.dashboard-news
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$DEST"
  echo "外しました"
  exit 0
fi

mkdir -p "$ROOT/logs" "$HOME/Library/LaunchAgents"
sed "s|__RUNNER_DIR__|$ROOT|g" "$ROOT/launchd/$LABEL.plist" > "$DEST"
launchctl bootstrap "gui/$(id -u)" "$DEST"
echo "登録しました: $DEST（毎日 15:30。ログは $ROOT/logs/）"
