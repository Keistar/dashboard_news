#!/bin/bash
# 毎日のニュースとトレンドを、この Mac から集めて更新する（README「ローカルでの毎日の更新」）。
#
#   1. scripts/news_pipeline.py collect   … Google News・Google トレンド・百度の候補を集める（通信はスクリプトだけ）
#   2. ジャンルごとに claude -p            … 記事を選び、日本語の見出しと要約を書く（prompts/*.md）
#   3. scripts/generate_dashboard.py      … ページと JSON を作る
#   4. ブランチに push → PR → マージ        … GitHub Pages に出る
#
# Claude に許すのは、work/ の中の読み書きと、news_pipeline.py の resolve / build だけ（--permission-mode dontAsk）。
# 記事ページの文に指示が紛れていても、ほかのコマンドやネットワークには届かない。
#
# 使い方: scripts/run_daily.sh [YYYY-MM-DD] [ジャンル ...]
#   日付を省くと今日（日本時間）。ジャンルを省くと全ジャンル＋トレンド。
#   NEWS_MODEL を入れると claude の --model に渡す。NEWS_NO_PUBLISH=1 なら PR を作らない（試すとき）。
#   NEWS_BASE_REF で始めるコミットを変えられる（既定 origin/main。ブランチで試すとき）。
set -uo pipefail

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export LANG=ja_JP.UTF-8

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1

DATE="${1:-$(TZ=Asia/Tokyo date +%F)}"
shift || true
mkdir -p logs
LOG="$ROOT/logs/$DATE.log"
exec > >(tee -a "$LOG") 2>&1

notify() {
  osascript -e "display notification \"$1\" with title \"dashboard_news\"" >/dev/null 2>&1 || true
}
fail() {
  echo "失敗: $1"
  notify "更新に失敗しました: $1（logs/$DATE.log）"
  exit 1
}

# 同時に 2 つ走らせない。
LOCK="$ROOT/work/.lock"
mkdir -p "$ROOT/work"
if ! mkdir "$LOCK" 2>/dev/null; then
  fail "前の実行がまだ終わっていない（終わっていなければ work/.lock を消す）"
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

echo "=== $(date '+%F %T') $DATE の更新を始める"

# .pyc を書かせない。書いたファイルで作業ツリーが汚れると、次の日から下の検査で止まる
# （2026-10-07・10-08 に、git に入っていた scripts/__pycache__ の .pyc が書き換わって止まった）
export PYTHONDONTWRITEBYTECODE=1

# --- 最新の main から始める（この clone は自動実行専用にする。手で作業する clone とは分ける）
[ -z "$(git status --porcelain --untracked-files=no)" ] || fail "作業ツリーに変更がある"
BASE="${NEWS_BASE_REF:-origin/main}"
git fetch -q origin || fail "git fetch"
git checkout -q -B "news/$DATE" "$BASE" || fail "git checkout"
# 2 週間より古い作業ファイルは消す。
find work -mindepth 1 -maxdepth 1 -type d -mtime +14 -exec rm -rf {} + 2>/dev/null

# --- Python の環境（Google News のリンクを元記事の URL に直すライブラリが要る）
if [ ! -x .venv/bin/python3 ]; then
  python3 -m venv .venv && .venv/bin/pip -q install -r requirements-local.txt || fail "venv の用意"
fi
export PATH="$ROOT/.venv/bin:$PATH"

GENRES=("$@")
if [ ${#GENRES[@]} -eq 0 ]; then
  read -r -a GENRES <<<"$(python3 -c 'import sys; sys.path.insert(0, "scripts"); from generate_dashboard import GENRES; print(" ".join(GENRES))') trends"
fi

python3 scripts/news_pipeline.py collect --date "$DATE" || fail "候補を集められない"

# --- ジャンルごとに Claude に書かせる。1 つ失敗しても、ほかのジャンルは続ける（失敗したジャンルは前日のまま）
MODEL_ARGS=()
[ -n "${NEWS_MODEL:-}" ] && MODEL_ARGS=(--model "$NEWS_MODEL")
FAILED=()
for g in "${GENRES[@]}"; do
  if [ "$g" = "trends" ]; then
    prompt="$(sed "s/{{DATE}}/$DATE/g" prompts/trends.md)"
  else
    meta="$(python3 -c "import sys; sys.path.insert(0, 'scripts'); from generate_dashboard import GENRES; c = GENRES['$g']; print(c['label_ja'] + '|' + ' / '.join(x['label_ja'] for x in c['countries']))")"
    prompt="$(sed -e "s/{{DATE}}/$DATE/g" -e "s/{{GENRE}}/$g/g" -e "s|{{LABEL}}|${meta%%|*}|g" -e "s|{{COUNTRIES}}|${meta#*|}|g" prompts/genre.md)"
  fi

  echo "--- $(date '+%T') $g"
  rm -f "work/$DATE/input/$g.json"
  # 1 ジャンル 25 分で打ち切る（macOS には timeout が無いので perl の alarm）。
  perl -e 'alarm shift; exec @ARGV' 1500 \
    claude -p "$prompt" ${MODEL_ARGS[@]+"${MODEL_ARGS[@]}"} \
      --permission-mode dontAsk \
      --setting-sources project \
      --strict-mcp-config \
      --no-session-persistence \
      --allowedTools \
        "Read(./work/**)" "Edit(./work/**)" "Glob" "Grep" \
        "Bash(python3 scripts/news_pipeline.py resolve --date $DATE --genre $g)" \
        "Bash(python3 scripts/news_pipeline.py build --date $DATE --genre $g)" \
    | tail -5

  if [ -s "work/$DATE/input/$g.json" ]; then
    python3 scripts/generate_dashboard.py --genre "$g" --input "work/$DATE/input/$g.json" --date "$DATE" | tail -1 || FAILED+=("$g")
  else
    echo "$g: input ができなかった"
    FAILED+=("$g")
  fi
done

# --- 公開（PR を作ってマージする。GitHub Pages に出る）
if [ -z "$(git status --porcelain docs)" ]; then
  fail "どのジャンルも更新できなかった"
fi
if [ "${NEWS_NO_PUBLISH:-}" = "1" ]; then
  echo "NEWS_NO_PUBLISH=1 なので公開しない（docs/ の変更はブランチ news/$DATE に残っている）"
  exit 0
fi

git add docs
git commit -q -m "${DATE}の各国ニュース・トレンドを更新（ローカルの定時実行）

更新できなかったジャンル: ${FAILED[*]:-なし}" || fail "git commit"
git push -q -f origin "news/$DATE" || fail "git push"
url="$(gh pr create --base main --head "news/$DATE" --title "${DATE}の各国ニュース・トレンドを更新" \
  --body "scripts/run_daily.sh による定時更新。更新できなかったジャンル: ${FAILED[*]:-なし}")" || fail "PR を作れない"
gh pr merge "$url" --merge --delete-branch >/dev/null || fail "PR をマージできない（${url}）"
# gh pr merge --delete-branch が手元のブランチも消す。
git checkout -q --detach "$BASE"; git branch -q -D "news/$DATE" 2>/dev/null || true

echo "=== $(date '+%F %T') 完了: ${url}（更新できなかったジャンル: ${FAILED[*]:-なし}）"
if [ ${#FAILED[@]} -gt 0 ]; then
  notify "更新しました。できなかったジャンル: ${FAILED[*]}"
fi
