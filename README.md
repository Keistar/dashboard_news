# AI News Dashboard (US Edition)

毎朝6:00（JST）に、ClaudeがWeb検索でアメリカ発のAI業界ニュースをピックアップし、
`docs/index.html` のダッシュボードを自動更新します。

## セットアップ

1. GitHubに新しいリポジトリを作成し、ここにあるファイル一式を追加する
2. Anthropic Consoleで新しいAPIキーを発行する
   （例: `ai-news-dashboard-us`。チャットなど平文の場所には貼らないこと）
3. リポジトリの **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `ANTHROPIC_API_KEY_NEWS_US`
   - Value: 発行したキー
4. **Settings → Actions → General → Workflow permissions** で
   「Read and write permissions」を選択して保存
   （ワークフローが自動コミットするために必要）
5. **Settings → Pages**
   - Source: `Deploy from a branch`
   - Branch: `main` ／ フォルダ: `/docs`
6. **Actions** タブ → `Daily AI News Dashboard` → `Run workflow` で試し実行

成功すると数分後に `https://<ユーザー名>.github.io/<リポジトリ名>/` で見られます。

## 仕組み

| ファイル | 役割 |
|---|---|
| `.github/workflows/daily-ai-news.yml` | 毎日21:00 UTC（=6:00 JST）に起動するスケジューラ |
| `scripts/generate_dashboard.py` | Claudeにweb_searchツールで検索させ、JSONで受け取ってHTML化 |
| `docs/index.html` | 生成されたダッシュボード本体（GitHub Pagesで公開） |
| `requirements.txt` | 必要なPythonパッケージ（`anthropic` SDK） |

## カスタマイズ

- ニュース件数や検索回数の上限は `generate_dashboard.py` 冒頭の
  `MAX_STORIES` / `max_uses` を変更
- ニュースの対象トピック（例: ハードウェア寄りに絞る等）は
  `SYSTEM_PROMPT` の文章を編集
- 配色やレイアウトは同ファイル内の `PAGE_TEMPLATE` のCSSを編集

## 注意

- GitHub Actionsのscheduleは混雑時に最大30〜60分程度ずれることがあります
- 1回の実行コストはおおよそ$0.1〜0.2程度の見込み（Sonnet 4.6・検索最大6回の場合）
- ニュース取得やJSON解析に失敗した場合はその日の更新をスキップし、
  前回のダッシュボードがそのまま残ります（壊れた状態で公開されません）
