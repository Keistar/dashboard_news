#!/usr/bin/env bash
# ローカルPCで毎朝実行するためのスクリプト（cron / タスクスケジューラ / launchd から呼ぶ）。
#   1. main を最新化  2. 全ジャンルをRSSから生成  3. docs/ をコミットして push
# 必要: git, python3, `pip install -r requirements.txt`, 環境変数 ANTHROPIC_API_KEY
# 例(cron, 毎朝6:00):  0 6 * * * ANTHROPIC_API_KEY=sk-... /path/to/dashboard_news/scripts/run_local.sh >> ~/dashboard_news.log 2>&1
set -u
cd "$(dirname "$0")/.."

git checkout main && git pull --ff-only origin main || { echo "git pull failed"; exit 1; }

for g in ai gadget kosodate beauty food health pet outdoor bousai; do
  python3 scripts/rss_news.py --genre "$g" || echo "WARN: $g failed"
done

git add docs/
if git diff --staged --quiet; then
  echo "no changes"
  exit 0
fi
git commit -m "Update news $(date +'%Y-%m-%d')"
git push origin main
