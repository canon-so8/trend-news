# NewsClaw

技術トレンドニュースを自動収集し、Jekyll（GitHub Pages）で配信する静的サイト。

## アーキテクチャ

- **静的サイト**: Jekyll + GitHub Pages (`canon-so8.github.io/news-claw`)
- **テーマ**: minima (dark skin)
- **自動収集**: GitHub Actions (`workflow_dispatch`) を外部cron（cron-job.org）から発火
- **通知**: Slack Bot で収集完了を通知

## ディレクトリ構成

```
scripts/                  # 収集スクリプト（Python）
  collect_daily_news.py   # デイリーニュース（Zenn/Qiita/はてな/HN）
  collect_hf_papers.py    # HF Daily Papers（arxiv論文）
_posts/
  daily_news/             # デイリーニュース記事
  arxiv/                  # HF Papers 記事
.github/workflows/
  collect-trends.yml      # 収集ワークフロー（祝日スキップ付き）
index.html                # 記事一覧ページ
```

## データソース

| タブ | ソース | API/形式 |
|------|--------|----------|
| Zenn | zenn.dev | API (liked_count) |
| Qiita | qiita.com | API v2 (likes_count) |
| はてな | hatenablog | RSS/RDF (bookmarkcount) |
| HN | Hacker News | Algolia API + DeepL翻訳 |
| HF Papers | Hugging Face Daily Papers | HF API (upvote/GitHub stars) |
| GitHub | GitHub Trending | スクレイピング |
| Kindle | Amazon Kindle日替わりセール | スクレイピング |

## 環境変数・シークレット

- `DEEPL_AUTH_KEY` / `DEEPL_ENABLED` — HNタイトル日本語訳
- `QIITA_TOKEN` — Qiita API認証
- `SLACK_BOT_TOKEN` / `SLACK_CHANNEL_ID` — Slack通知
- `SITE_URL` — ページURL生成用（_config.ymlから自動取得）

## 開発メモ

- Python実行: `C:\Users\USER228\.venvs\ideacon\Scripts\python.exe`
- 収集スクリプトは外部依存を最小限に（requests, beautifulsoup4, lxml, defusedxml）
- 商用メディア（ITmedia・東洋経済等）はSlack配信の利用規約リスクのため除外済み
- 祝日はhttps://holidays-jp.github.io APIで判定しスキップ
