# セキュリティレビューレポート
> レビュー日時: 2026-04-05 21:09 JST
> 対象: `canon-so8/news-claw` (全ソースファイル)

## サマリー
- 重大 (Critical): 0件
- 高 (High): 1件
- 中 (Medium): 3件
- 低 (Low): 3件
- 情報 (Info): 3件

---

## 検出事項

### [高] XML外部エンティティ (XXE) 攻撃の可能性
- **ファイル**: `scripts/collect_daily_news.py:302-309`
- **カテゴリ**: OWASP A03 インジェクション
- **説明**: `xml.etree.ElementTree.fromstring()` を使用してRSSフィードのXMLをパースしている。外部のRSSフィード（はてなブックマーク等）から取得したXMLを直接パースしており、悪意あるXMLが注入された場合にXXE攻撃を受ける可能性がある。
- **影響**: サーバー側のファイル読み取り（GitHub Actionsランナー上）、SSRF攻撃。ただし実行環境がGitHub Actions上の一時的なランナーのため、影響は限定的。
- **修正案**: `defusedxml` パッケージを使用する。
  ```python
  # pip install defusedxml
  from defusedxml import ElementTree as ET
  ```
  または、`xml.etree.ElementTree` を使い続ける場合は以下で外部エンティティを無効化:
  ```python
  parser = ET.XMLParser()
  # Python 3.8+ では外部エンティティはデフォルトで無効だが、明示的に設定推奨
  ```
  > **補足**: Python 3.8以降の `xml.etree.ElementTree` はデフォルトで外部エンティティ展開が無効化されているため、実際のリスクは低い。ただし防御的プログラミングとして `defusedxml` の使用を推奨。

---

### [中] Slack Bot Token のログ出力リスク
- **ファイル**: `.github/workflows/collect-trends.yml:93-94`
- **カテゴリ**: OWASP A09 ログとモニタリングの不備
- **説明**: Slack API のレスポンスを `print(json.loads(...).get("ok"))` で出力している。正常時は `True`/`False` のみだが、APIエラー時にレスポンス全体がログに記録される可能性がある（トークン自体は含まれないが、チャンネル情報等が漏洩しうる）。
- **影響**: GitHub Actions のログにSlack関連の情報が記録される可能性。パブリックリポジトリの場合、誰でもログを閲覧可能。
- **修正案**: エラーハンドリングを追加し、レスポンス全体ではなく成否のみを出力する。また、リポジトリがパブリックの場合はワークフローログの公開範囲を確認する。

---

### [中] SSRF (Server-Side Request Forgery) の限定的リスク
- **ファイル**: `scripts/collect_daily_news.py:270-277`, `scripts/collect_hf_papers.py:154-168`
- **カテゴリ**: OWASP A10 SSRF
- **説明**: RSSフィードやAPIから取得したURL（`item["url"]` 等）をそのまま使用している。現状はデータの表示目的のみで、サーバー側からこれらのURLにアクセスし直すことはないため、直接的なSSRFリスクはない。ただし、`collect_daily_news.py` の `collect_qiita()` (L486-492) では、RSSから取得したURLからIDを抽出してQiita APIを呼び出しており、フィードが改ざんされた場合に意図しないAPIエンドポイントを呼ぶ可能性がある。
- **影響**: 限定的。攻撃者がRSSフィードを改ざんできる場合のみ成立。
- **修正案**: URLのドメインをホワイトリストで検証する。
  ```python
  from urllib.parse import urlparse
  ALLOWED_DOMAINS = {"qiita.com", "zenn.dev", ...}
  parsed = urlparse(url)
  if parsed.hostname not in ALLOWED_DOMAINS:
      continue
  ```

---

### [中] HTML出力のXSS (Cross-Site Scripting) 対策の部分的不足
- **ファイル**: `scripts/collect_daily_news.py:731-732`, `scripts/collect_hf_papers.py:276-289`
- **カテゴリ**: OWASP A03 インジェクション (XSS)
- **説明**:
  - `collect_daily_news.py` では `esc()` 関数でHTMLエスケープを実施しており、基本的な対策はできている。
  - しかし `collect_hf_papers.py` では **タイトルやサマリーに対するHTMLエスケープが行われていない** (L280, L287)。arXivの論文タイトルに `<script>` 等が含まれる可能性は低いが、防御的にエスケープすべき。
  - また、URL部分（`href` 属性）にはエスケープが適用されていないため、`javascript:` スキームによるXSSのリスクがある（ただしデータソースがHF API / arXivのため、実際のリスクは極めて低い）。
- **影響**: GitHub Pages上で表示されるため、閲覧者のブラウザでスクリプト実行の可能性（理論上）。
- **修正案**: `collect_hf_papers.py` にもHTMLエスケープを追加する。
  ```python
  from html import escape
  # タイトル、サマリー等に適用
  title = escape(p["title"])
  ```

---

### [低] DeepL APIキーのハードコード防止は適切だが、環境変数の取り扱いに注意
- **ファイル**: `scripts/collect_hf_papers.py:90`, `scripts/collect_daily_news.py:363`
- **カテゴリ**: OWASP A02 暗号化の失敗
- **説明**: APIキーは `os.environ.get()` で環境変数から取得しており、ハードコードはされていない（適切）。GitHub Actions では `secrets` を使って注入されている（適切）。ただし、デバッグ用の `print` 文でAPIキーが意図せず出力されないよう注意が必要。現状のコードではキー自体の出力はない。
- **影響**: 現状は問題なし。
- **修正案**: 現状維持で問題ない。将来の変更時にAPIキーをログに出力しないよう注意すること。

---

### [低] ユーザーエージェント偽装
- **ファイル**: `scripts/collect_daily_news.py:261`, `scripts/collect_hf_papers.py:148`
- **カテゴリ**: セキュリティ設定
- **説明**: `User-Agent` ヘッダーをブラウザに偽装している。セキュリティ上の脆弱性ではないが、利用するAPIの利用規約に抵触する可能性がある。
- **影響**: API提供者のBot検出・ブロックの対象となる可能性。
- **修正案**: 可能であれば正規のUser-Agentを使用する（例: `news-claw/1.0`）。特にHugging Face APIやQiita APIは正規のUA使用でも問題ないはず。

---

### [低] `/tmp` ファイルの使用
- **ファイル**: `scripts/collect_hf_papers.py:308`, `scripts/collect_daily_news.py:888`
- **カテゴリ**: セキュリティ設定
- **説明**: `/tmp` にSlack通知用のURLファイルを書き出している。GitHub Actions ランナー上では問題ないが、他の環境で実行する場合、`/tmp` はシステム共有ディレクトリのためシンボリックリンク攻撃等のリスクがある。
- **影響**: GitHub Actions環境では問題なし。ローカル実行時のリスクは非常に低い。
- **修正案**: `tempfile.mkstemp()` や環境変数での出力パス指定を検討。ただし優先度は低い。

---

### [情報] GitHub Actions のワークフロー権限
- **ファイル**: `.github/workflows/collect-trends.yml:9`
- **カテゴリ**: OWASP A05 セキュリティ設定ミス
- **説明**: `permissions: contents: write` が設定されており、ワークフローがリポジトリに書き込み可能。自動コミット＆プッシュの目的では必要な権限。最小権限の原則に則っている。
- **影響**: なし（適切な設定）。

---

### [情報] 外部APIへの依存
- **カテゴリ**: サプライチェーン
- **説明**: 以下の外部APIに依存している:
  - Hugging Face API (`huggingface.co`)
  - Zenn API (`zenn.dev`)
  - Qiita API (`qiita.com`)
  - はてなブックマーク RSS (`b.hatena.ne.jp`)
  - Hacker News Algolia API (`hn.algolia.com`)
  - DeepL API (`api-free.deepl.com` / `api.deepl.com`)
  - Slack API (`slack.com`)
  - 祝日API (`holidays-jp.github.io`)
- **影響**: いずれかのAPIが停止・変更された場合にデータ収集が失敗する。現状の `try/except` とリトライ機構で適切に対処されている。

---

### [情報] 依存パッケージの確認
- **カテゴリ**: OWASP A06 脆弱なコンポーネント
- **説明**: `pip install requests beautifulsoup4 lxml` でインストールされるパッケージ。バージョン固定がされていないため、ビルド時に最新版がインストールされる。`requirements.txt` でバージョンを固定することを推奨。
- **修正案**:
  ```
  # requirements.txt
  requests==2.32.x
  beautifulsoup4==4.12.x
  lxml==5.x.x
  ```
  バージョンを固定しつつ、Dependabot等で定期的に更新チェックを行う。

---

## 推奨事項（優先度順）

1. **`collect_hf_papers.py` にHTMLエスケープを追加する** — XSSリスクの排除（中・対応容易）
2. **`defusedxml` の導入を検討する** — XXE対策の強化（高だが実際のリスクは低い・対応容易）
3. **`requirements.txt` でパッケージバージョンを固定する** — サプライチェーンリスクの低減
4. **URLのドメインホワイトリスト検証を追加する** — SSRF対策
5. **User-Agentを正規のものに変更する** — API利用規約の遵守

---

> このレポートは自動生成されたものであり、全ての脆弱性を網羅している保証はありません。
> 本番環境へのデプロイ前には、専門家による追加のセキュリティレビューを推奨します。
