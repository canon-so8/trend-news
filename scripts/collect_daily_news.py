#!/usr/bin/env python3
"""
デイリーニュース収集スクリプト
Claude Code 不要 - RSS/API から直接データ取得してJekyll Markdownを生成

ソース:
  - Zenn   : API (liked_count付き)
  - Qiita  : API v2 (likes_count付き)
  - はてな  : RSS/RDF (bookmarkcount付き)
  - HN     : Algolia API + DeepL（タイトル日本語訳）

※ 商用メディア（ITmedia・東洋経済等）はSlack配信の利用規約リスクのため除外
"""
import json
import os
import re
import sys
import time
import urllib.parse
from defusedxml import ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

JST = timezone(timedelta(hours=9))
REPO_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = REPO_ROOT / "_posts" / "daily_news"

KINDLE_DAILY_URL = (
    "https://www.amazon.co.jp/kindle-dbs/browse/"
    "?_encoding=UTF8"
    "&metadata=storeType%3Debooks"
    "&widgetId=ebooks-deals-storefront_KindleDailyDealsStrategy"
    "&title=Kindle%E6%97%A5%E6%9B%BF%E3%82%8F%E3%82%8A%E3%82%BB%E3%83%BC%E3%83%AB"
    "&sourceType=recs"
)

# RSS 名前空間
RSS1   = "http://purl.org/rss/1.0/"
DC     = "http://purl.org/dc/elements/1.1/"
ATOM   = "http://www.w3.org/2005/Atom"
RDF    = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
HATENA = "http://www.hatena.ne.jp/info/xmlns#"

# --- タグ判定（複数タグ対応、全キーワードリストを走査してマッチしたタグを全て返す）---
AGENT_KEYWORDS = [
    # LLM/モデル名
    "claude ", "claude\u300c", "claude\u300d", "claude code",
    "gpt-4", "gpt-5", "gpt-4o", "chatgpt", "openai", "codex",
    "gemini ", "gemini-", "bard",
    "llama", "mistral", "phi-", "qwen", "deepseek",
    "llm", "大規模言語モデル", "言語モデル",
    "anthropic", "notebooklm",
    "rag ", "ファインチューニング", "fine-tun", "alignment",
    "chain-of-thought", "プロンプトエンジニアリング", "ハーネスエンジニアリング",
    "駆動開発",
    # エージェント
    "ai agent", "agentic", "multi-agent", "マルチエージェント",
    "aiエージェント", "llmエージェント", "自律エージェント",
    "mcp", "tool use", "tool calling", "function calling",
    "langgraph", "langchain", "crewai", "autogen", "dspy", "smolagents",
    "computer use", "browser use",
    "workflow automation", "オーケストレーション",
]
AI_KEYWORDS   = [
    "生成ai", "aiネイティブ", "ai駆動",
    "人工知能", "生成モデル", "画像生成ai", "動画生成ai",
    "ai活用", "aiを活用", "aiで", "aiが", "ai搭載", "aiツール",
    "aiアシスタント", "aiサービス", "ai開発", "ai導入",
]
# copilot/cursor/agentは文脈で分かれるため、AI専用キーワードと組み合わせて判定
AI_COMBO_KEYWORDS = ["copilot", "cursor "]  # agentはAGENT_KEYWORDSで処理
ML_KEYWORDS   = [
    "machine learning", "deep learning", "reinforcement learning", "pytorch", "tensorflow",
    "kaggle", "neural network", "transformer", "bert", "llama", "optimizer",
    "機械学習", "深層学習", "強化学習", "ニューラル",
    "統計学", "統計的", "回帰分析", "分類問題", "クラスタリング", "特徴量",
    "学習率", "活性化関数", "損失関数", "バックプロパゲーション",
    "scikit", "xgboost", "lightgbm", "catboost", "データサイエンス",
    "過学習", "正則化", "バッチ正規化", "アテンション機構",
    "特徴抽出", "埋め込みモデル", "テキスト埋め込み", "ベイズ", "tabnet",
    "分散並列", "並列学習", "コンペ", "モデル学習", "自然言語処理",
    "音声認識", "音声合成", "音声処理", "音響", "asr", "tts", "speech recognition",
    "speech synthesis", "音声モデル","kaggle"
]
CV_KEYWORDS   = [
    "image", "video", "vision", "diffusion", "gan", "vae", "3d reconstruction",
    "yolo", "resnet", "vit ", "pose estimation", "depth estimation",
    "画像認識", "画像生成", "動画生成", "物体検出", "セグメンテーション", "ocr",
    "コンピュータビジョン", "点群", "自動運転", "autonomous driving",
    "ロボット", "robot", "lerobot", "3次元",
]
POEM_KEYWORDS = [
    "エンジニア哲学", "仕事術", "ポエム", "エンジニアの心得",
    "生き方", "働き方", "マインドセット", "組織論", "チームビルディング",
    "リーダーシップ", "マネジメント論",
    "エンジニアとして", "技術者として", "プログラマとして",
    "未経験から", "未経験でも", "新人エンジニア",
    "折れない", "後悔しない", "成長するため",
    "してみた", "触ってみた", "考えてみた", "使ってみた", "試してみた",
    "やってみた", "振り返って", "振り返り",
    "な話", "という話", "った話", "した話","話","考え","できた"
]
ECO_KEYWORDS  = [
    "経済", "半導体", "nvidia", "tsmc", "テック企業", "産業動向", "規制",
    "政策", "株価", "business", "startup", "スタートアップ", "vc ", "融資",
    "ipo", "資金調達", "市場規模", "シェア", "競合", "買収", "合併",
    "apple", "google", "microsoft", "meta ", "amazon", "tesla",
]
GIT_KEYWORDS  = [
    "git ", "github", "gitlab", "gitflow", "git-",
    "pull request", "プルリクエスト", "マージリクエスト",
    "ブランチ戦略", "バージョン管理", "コード管理",
    "github actions", "git hooks", "git submodule", "git rebase",
]
PKG_KEYWORDS  = [
    "npm", "pip ", "yarn", "pnpm", "cargo ",
    "pypi", "homebrew", "brew ",
    "パッケージ", "依存関係", "ライブラリ更新", "パッケージ管理",
    "package manager", "dependency", "poetry ", "uv ",
    "requirements.txt", "package.json", "go.mod", "composer",
]
SEC_KEYWORDS  = [
    "セキュリティ", "security", "脆弱性", "vulnerability", "cve-",
    "サプライチェーン攻撃", "ゼロデイ", "zero-day", "マルウェア", "malware",
    "暗号化", "encryption", "認証", "authentication", "oauth", "oidc",
    "xss", "csrf", "sql injection", "ペネトレーション", "penetration",
    "crowdstrike", "soc ", "siem", "ゼロトラスト", "zero trust",
    "ssh", "ssl", "tls", "証明書", "certificate",
]
CLOUD_KEYWORDS = [
    "aws", "gcp", "azure", "クラウド", "cloud",
    "kubernetes", "k8s", "docker", "コンテナ",
    "デプロイ", "インフラ", "terraform", "cloudflare",
    "サーバーレス", "lambda", "cloud run", "fargate",
    "マイクロサービス", "microservice",
]
LANG_KEYWORDS = [
    "python", "javascript", "typescript", "rust", "go ", "golang",
    "java ", "kotlin", "swift", "c++", "c#", "ruby", "elixir", "zig",
    "haskell", "scala", "dart", "lua", "perl", "r言語",
]
FRONT_KEYWORDS = [
    "react", "vue", "svelte", "next.js", "nuxt", "angular", "astro",
    "フロントエンド", "frontend", "css", "html", "tailwind", "sass",
    "レスポンシブ", "responsive", "spa ", "ssr ", "dom ", "webcomponent",
    "ui/ux", "デザインシステム", "コンポーネント", "storybook",
    "ブラウザ", "browser", "web api", "webassembly", "wasm",
]
BACK_KEYWORDS  = [
    "バックエンド", "backend", "サーバーサイド", "server-side",
    "データベース", "sql", "postgresql", "mysql", "redis", "mongodb",
    "api設計", "rest api", "restapi", "graphql", "grpc",
    "orm ", "migration", "テーブル設計", "スキーマ設計",
    "マイグレーション", "全文検索", "elasticsearch",
    "nginx", "apache", "express", "fastapi", "django", "rails",
]
RESEARCH_KEYWORDS = [
    "論文", "paper", "arxiv", "研究", "research",
    "学会", "conference", "icml", "neurips", "iclr", "cvpr", "aaai",
    "サーベイ", "survey", "ベンチマーク", "benchmark",
    "実験", "experiment", "提案手法", "先行研究", "state-of-the-art", "sota",
]
OSS_KEYWORDS   = [
    "oss", "オープンソース", "open source", "open-source",
    "コントリビュート", "contribute", "プルリクエスト",
    "license", "ライセンス", "mit license", "apache license",
    "公開しました", "リリースしました", "作りました",
    "自作", "個人開発", "趣味開発",
]
DEV_KEYWORDS  = [
    "linux", "cli", "sdk", "vscode",
    "ios開発", "android開発", "アプリ開発", "ios向け",
    "プログラミング", "コーディング", "ライブラリ", "フレームワーク",
    "バグ", "テスト", "ci/cd", "コマンドライン", "ターミナル", "シェルスクリプト",
    "bash ", "zsh", "makefile",
    "uuid", "スキーマ", "パフォーマンス",
    "リファクタリング", "コードレビュー", "開発環境", "wsl",
    "ログ設計", "ログ収集", "監視", "オブザーバビリティ",
    "hostsファイル",
]

TAG_LABELS = {
    "agent":    ("Agent",        "tag-agent"),
    "ai":       ("AI",           "tag-ai"),
    "ml":       ("機械学習",      "tag-ml"),
    "cv":       ("画像",         "tag-cv"),
    "research": ("研究",         "tag-research"),
    "sec":      ("セキュリティ",   "tag-sec"),
    "poem":     ("ポエム",        "tag-poem"),
    "eco":      ("経済",         "tag-eco"),
    "front":    ("フロントエンド", "tag-front"),
    "back":     ("バックエンド",   "tag-back"),
    "cloud":    ("クラウド",      "tag-cloud"),
    "lang":     ("言語",         "tag-lang"),
    "git":      ("Git",          "tag-git"),
    "pkg":      ("パッケージ",     "tag-pkg"),
    "oss":      ("OSS",          "tag-oss"),
    "dev":      ("開発",         "tag-dev"),
    "other":    ("その他",       "tag-other"),
}


_TAG_RULES: list[tuple[str, list[str]]] = [
    ("agent",    AGENT_KEYWORDS),
    ("ai",       AI_KEYWORDS),
    ("ml",       ML_KEYWORDS),
    ("cv",       CV_KEYWORDS),
    ("research", RESEARCH_KEYWORDS),
    ("sec",      SEC_KEYWORDS),
    ("poem",     POEM_KEYWORDS),
    ("eco",      ECO_KEYWORDS),
    ("front",    FRONT_KEYWORDS),
    ("back",     BACK_KEYWORDS),
    ("cloud",    CLOUD_KEYWORDS),
    ("lang",     LANG_KEYWORDS),
    ("git",      GIT_KEYWORDS),
    ("pkg",      PKG_KEYWORDS),
    ("oss",      OSS_KEYWORDS),
    ("dev",      DEV_KEYWORDS),
]


def classify_tags(title: str, desc: str = "") -> list[str]:
    """タイトル+説明文からマッチする全タグを返す（複数タグ対応）"""
    text = (title + " " + desc).lower()
    tags: list[str] = []
    for tag_key, keywords in _TAG_RULES:
        if tag_key == "ai":
            has_direct = any(k in text for k in AI_KEYWORDS)
            has_combo  = any(k in text for k in AI_COMBO_KEYWORDS) and has_direct
            if has_direct or has_combo:
                tags.append("ai")
        elif any(k in text for k in keywords):
            tags.append(tag_key)
    return tags or ["other"]  # 未分類は「その他」


def tag_spans(tag_keys: list[str]) -> str:
    """複数タグをスパンHTML文字列に変換"""
    parts = []
    for k in tag_keys:
        label, cls = TAG_LABELS.get(k, ("Other", "tag-other"))
        parts.append(f'<span class="tag {cls}">{label}</span>')
    return " ".join(parts)


# --- HTTP セッション ---
QIITA_TOKEN = os.environ.get("QIITA_TOKEN", "")

ALLOWED_DOMAINS = {
    "qiita.com", "zenn.dev", "b.hatena.ne.jp",
    "hn.algolia.com", "news.ycombinator.com",
    "api-free.deepl.com", "api.deepl.com",
    "yuji.software",
    "yashikota.github.io",
}


def is_allowed_url(url: str) -> bool:
    """URLのドメインがホワイトリストに含まれるか検証"""
    try:
        hostname = urlparse(url).hostname or ""
        return any(hostname == d or hostname.endswith("." + d) for d in ALLOWED_DOMAINS)
    except Exception:
        return False


def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    repo_url = os.environ.get("REPO_URL", "https://github.com/news-claw")
    headers = {"User-Agent": f"news-claw/1.0 (+{repo_url})"}
    if QIITA_TOKEN:
        headers["Authorization"] = f"Bearer {QIITA_TOKEN}"
    s.headers.update(headers)
    return s

SESSION = _session()


def get(url: str, timeout: int = 20, **kwargs) -> Optional[requests.Response]:
    try:
        r = SESSION.get(url, timeout=timeout, **kwargs)
        r.raise_for_status()
        return r
    except Exception as e:
        print(f"  fetch error [{url[:70]}]: {e}", file=sys.stderr)
        return None


# --- RSS パーサー ---
def _text(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def _parse_date(raw: str) -> str:
    """RSS 2.0 の pubDate (RFC 2822) や ISO 日付を YYYY-MM-DD に正規化"""
    if not raw:
        return ""
    if re.match(r'\d{4}-\d{2}-\d{2}', raw):
        return raw[:10]
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(raw).strftime("%Y-%m-%d")
    except Exception:
        return raw[:10]


def parse_rss(data: bytes) -> list[dict]:
    """RSS 1.0(RDF) / RSS 2.0 / Atom を解析。
    はてブRDFの場合は hatena:bookmarkcount も返す。"""
    items = []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        try:
            text = data.decode("utf-8", errors="replace")
            if "?>" in text:
                text = text[text.index("?>") + 2:]
            root = ET.fromstring(text)
        except Exception:
            return items

    ns = root.tag

    # --- Atom ---
    if root.tag == f"{{{ATOM}}}feed":
        for entry in root.findall(f"{{{ATOM}}}entry"):
            title_el = entry.find(f"{{{ATOM}}}title")
            link_el  = entry.find(f"{{{ATOM}}}link")
            _pub     = entry.find(f"{{{ATOM}}}published")
            pub_el   = _pub if _pub is not None else entry.find(f"{{{ATOM}}}updated")
            _sum     = entry.find(f"{{{ATOM}}}summary")
            sum_el   = _sum if _sum is not None else entry.find(f"{{{ATOM}}}content")
            title = _text(title_el)
            url   = link_el.get("href") or _text(link_el) if link_el is not None else ""
            pub   = _text(pub_el)[:10]
            desc  = _text(sum_el)
            if title and url:
                items.append({"title": title, "url": url, "date": pub, "desc": desc, "meta": {}})
        return items

    # --- RSS 1.0 (RDF) ---
    if root.tag == f"{{{RDF}}}RDF":
        for item in root.findall(f"{{{RSS1}}}item"):
            title  = _text(item.find(f"{{{RSS1}}}title"))
            url    = _text(item.find(f"{{{RSS1}}}link"))
            pub    = _text(item.find(f"{{{DC}}}date"))[:10]
            desc   = _text(item.find(f"{{{RSS1}}}description"))
            bmarks = _text(item.find(f"{{{HATENA}}}bookmarkcount"))
            if title and url:
                items.append({
                    "title": title, "url": url, "date": pub, "desc": desc,
                    "meta": {"bookmarks": int(bmarks) if bmarks.isdigit() else 0},
                })
        return items

    # --- RSS 2.0 ---
    _ch = root.find("channel")
    channel = _ch if _ch is not None else root
    for item in channel.findall("item"):
        title  = _text(item.find("title"))
        url    = _text(item.find("link"))
        _pd    = item.find("pubDate")
        pub_el = _pd if _pd is not None else item.find(f"{{{DC}}}date")
        pub    = _parse_date(_text(pub_el))
        desc   = _text(item.find("description"))
        if title and url:
            items.append({"title": title, "url": url, "date": pub, "desc": desc, "meta": {}})
    return items


# --- 翻訳 ---
DEEPL_AUTH_KEY = os.environ.get("DEEPL_AUTH_KEY", "")
DEEPL_ENABLED = os.environ.get("DEEPL_ENABLED", "false").lower() == "true"
DEEPL_API_BASE = (
    "https://api-free.deepl.com"
    if DEEPL_AUTH_KEY.endswith(":fx")
    else "https://api.deepl.com"
)
deepl_chars_used = 0


def translate_deepl(text: str) -> str:
    """DeepL API で英語→日本語翻訳"""
    global deepl_chars_used
    if not DEEPL_AUTH_KEY or not DEEPL_ENABLED or not text:
        return ""
    truncated = text[:500]
    try:
        resp = SESSION.post(
            f"{DEEPL_API_BASE}/v2/translate",
            headers={"Authorization": f"DeepL-Auth-Key {DEEPL_AUTH_KEY}"},
            data={"text": truncated, "target_lang": "JA"},
            timeout=15,
        )
        if resp.status_code == 200:
            deepl_chars_used += len(truncated)
            return resp.json()["translations"][0]["text"]
        print(f"  DeepL HTTP {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"  DeepL error: {e}", file=sys.stderr)
    return ""


def translate_ja(text: str) -> str:
    """DeepL で翻訳（無効時・失敗時は空文字）"""
    return translate_deepl(text)


# --- 各ソース収集 ---
ZENN_TOPICS = [
    "claudecode", "llm", "machinelearning", "deeplearning",
    "computervision", "kaggle", "audio", "nlp", "statistics", "pytorch", "agent",
]

QIITA_TAGS = [
    "MachineLearning", "DeepLearning", "ComputerVision", "Python",
    "kaggle", "NLP", "PyTorch", "音声認識", "統計", "LLM",
]


def collect_zenn() -> list[dict]:
    """Zenn API: ウィークリートレンドで取得してからいいね数でソート"""
    seen: set[str] = set()
    articles: list[dict] = []

    def _parse(a: dict) -> Optional[dict]:
        slug = a.get("slug", "")
        title = a.get("title", "")
        if not title or not slug:
            return None
        return {
            "title": title,
            "url": f"https://zenn.dev{a.get('path', f'/articles/{slug}')}",
            "date": (a.get("published_at") or "")[:10],
            "desc": "",
            "meta": {
                "likes": a.get("liked_count", 0) or 0,
                "author": (a.get("user") or {}).get("name", ""),
            },
        }

    def fetch_topic(topic: str) -> list[dict]:
        url = f"https://zenn.dev/api/articles?topicname={topic}&order=weekly&count=20"
        r = get(url)
        if not r:
            return []
        return [p for a in r.json().get("articles", []) if (p := _parse(a))]

    # Techs + Ideas（デイリートレンド）
    for atype in ("tech", "idea"):
        r = get(f"https://zenn.dev/api/articles?order=weekly&count=50&article_type={atype}")
        if r:
            for a in r.json().get("articles", []):
                p = _parse(a)
                if p and p["url"] not in seen:
                    seen.add(p["url"])
                    articles.append(p)

    with ThreadPoolExecutor(max_workers=6) as ex:
        for items in ex.map(fetch_topic, ZENN_TOPICS):
            for item in items:
                if item["url"] not in seen:
                    seen.add(item["url"])
                    articles.append(item)

    # いいね数降順でソート・タグ付け
    articles.sort(key=lambda a: a["meta"].get("likes", 0), reverse=True)
    for a in articles:
        a["tags"] = classify_tags(a["title"])
    return articles[:50]


def collect_qiita() -> list[dict]:
    """Qiita: popular-items/feed (Atom=トレンド順) + タグ別API補完"""
    seen: set[str] = set()
    trend_articles: list[dict] = []
    tag_articles: list[dict] = []

    # トレンドフィード（https://qiita.com/trend 相当、順序がトレンド順）
    r = get("https://qiita.com/popular-items/feed")
    if r:
        for item in parse_rss(r.content):
            item["url"] = _strip_utm(item["url"])
            if item["url"] and item["url"] not in seen:
                seen.add(item["url"])
                item["tags"] = classify_tags(item["title"], item["desc"])
                item["meta"].setdefault("likes", 0)
                item["meta"].setdefault("author", "")
                trend_articles.append(item)

    # トレンド記事のlikes_countをAPI経由で補完
    for a in trend_articles:
        if not is_allowed_url(a["url"]):
            continue
        # URLからitem_idを抽出: https://qiita.com/user/items/XXXXX → XXXXX
        parts = a["url"].rstrip("/").split("/")
        if len(parts) >= 2 and parts[-2] == "items":
            item_id = parts[-1]
            if not re.fullmatch(r'[a-zA-Z0-9_-]+', item_id):
                continue
            r2 = get(f"https://qiita.com/api/v2/items/{item_id}")
            if r2:
                data = r2.json()
                a["meta"]["likes"] = data.get("likes_count", 0) or 0
                a["meta"]["author"] = (data.get("user") or {}).get("id", "")
                a["date"] = (data.get("created_at") or "")[:10]

    # タグ別でトレンドに載らない専門記事も補完
    since = (datetime.now(JST) - timedelta(days=7)).strftime("%Y-%m-%d")

    def fetch_tag(tag: str) -> list[dict]:
        encoded = urllib.parse.quote(tag)
        url = f"https://qiita.com/api/v2/items?per_page=15&query=tag:{encoded}+created:>={since}+stocks:>3"
        r = get(url)
        if not r:
            return []
        result = []
        for it in r.json():
            art_url = it.get("url", "")
            title = it.get("title", "")
            if title and art_url:
                result.append({
                    "title": title, "url": art_url,
                    "date": (it.get("created_at") or "")[:10], "desc": "",
                    "tags": classify_tags(title),
                    "meta": {
                        "likes": it.get("likes_count", 0) or 0,
                        "author": (it.get("user") or {}).get("id", ""),
                    },
                })
        return result

    with ThreadPoolExecutor(max_workers=5) as ex:
        for items in ex.map(fetch_tag, QIITA_TAGS):
            for item in items:
                if item["url"] not in seen:
                    seen.add(item["url"])
                    tag_articles.append(item)

    # 全記事をいいね数順でソート
    all_articles = trend_articles + tag_articles
    all_articles.sort(key=lambda a: a["meta"].get("likes", 0), reverse=True)
    return all_articles[:40]


def collect_hatena() -> list[dict]:
    """はてなブックマーク: IT人気エントリー + キーワード検索RSS"""
    urls = [
        "https://b.hatena.ne.jp/hotentry/it.rss",
        # サブカテゴリRSSは廃止済みのためキーワード検索RSSで代替
        "https://b.hatena.ne.jp/q/AI?date_range=1w&sort=hot&mode=rss&safe=on&target=entry&users=3",
        "https://b.hatena.ne.jp/q/%E6%A9%9F%E6%A2%B0%E5%AD%A6%E7%BF%92?date_range=1w&sort=hot&mode=rss&safe=on&target=entry&users=3",
        "https://b.hatena.ne.jp/q/%E3%83%97%E3%83%AD%E3%82%B0%E3%83%A9%E3%83%9F%E3%83%B3%E3%82%B0?date_range=1w&sort=hot&mode=rss&safe=on&target=entry&users=3",
    ]
    seen: set[str] = set()
    articles: list[dict] = []

    def fetch_one(url: str) -> list[dict]:
        r = get(url)
        return parse_rss(r.content) if r else []

    with ThreadPoolExecutor(max_workers=4) as ex:
        for items in ex.map(fetch_one, urls):
            for item in items:
                if item["url"] not in seen:
                    seen.add(item["url"])
                    item["tags"] = classify_tags(item["title"], item["desc"])
                    articles.append(item)

    articles.sort(key=lambda a: a["meta"].get("bookmarks", 0), reverse=True)
    return articles[:30]


def collect_hatena_blog() -> list[dict]:
    """はてなブログ注目記事: はてブ検索RSSでエンジニア系ブログ記事を収集"""
    keywords = [
        "エンジニア",
        "プログラマ",
        "技術ブログ",
    ]
    seen: set[str] = set()
    articles: list[dict] = []

    def fetch_keyword(kw: str) -> list[dict]:
        encoded = urllib.parse.quote(kw)
        url = f"https://b.hatena.ne.jp/q/{encoded}?date_range=1m&sort=hot&mode=rss&safe=on&target=entry&users=5"
        r = get(url)
        return parse_rss(r.content) if r else []

    with ThreadPoolExecutor(max_workers=3) as ex:
        for items in ex.map(fetch_keyword, keywords):
            for item in items:
                if item["url"] not in seen:
                    seen.add(item["url"])
                    item["tags"] = classify_tags(item["title"], item["desc"])
                    articles.append(item)

    articles.sort(key=lambda a: a["meta"].get("bookmarks", 0), reverse=True)
    return articles[:20]


def collect_hn() -> list[dict]:
    """Algolia API でHacker Newsの記事を収集（当日のみ）"""
    # pts>=100 かつ直近24時間以内の記事のみ
    since_ts = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp())
    url = (
        "https://hn.algolia.com/api/v1/search"
        f"?tags=story&numericFilters=points%3E%3D100%2Ccreated_at_i%3E{since_ts}&hitsPerPage=30"
        "&attributesToRetrieve=title,url,points,num_comments,created_at,objectID"
    )
    r = get(url)
    if not r:
        return []
    articles = []
    for h in r.json().get("hits", []):
        title = h.get("title", "")
        story_url = h.get("url", "")
        if not title or not story_url:
            continue
        hn_url = f"https://news.ycombinator.com/item?id={h.get('objectID', '')}"
        articles.append({
            "title": title,
            "url": story_url,
            "date": (h.get("created_at") or "")[:10],
            "desc": "",
            "tags": classify_tags(title),
            "meta": {
                "points": h.get("points", 0),
                "comments": h.get("num_comments", 0),
                "hn_url": hn_url,
                "title_ja": "",
            },
        })

    # タイトル翻訳（DEEPL_ENABLED時のみ翻訳、それ以外はスキップ）
    if DEEPL_ENABLED:
        print(f"  HN翻訳中... ({len(articles)}件)")
        for a in articles:
            a["meta"]["title_ja"] = translate_ja(a["title"])
            time.sleep(0.15)
    else:
        print(f"  HN翻訳スキップ（DEEPL_ENABLED=false）")

    return articles


def collect_slides() -> list[dict]:
    """勉強会スライドbot: JSON Feed からスライド情報を収集
    データソース: https://github.com/YujiSoftware/tech_slideshare
    """
    url = "https://yuji.software/tech_slideshare/feed.json"
    r = get(url)
    if not r:
        return []
    articles = []
    seen: set[str] = set()
    for item in r.json().get("items", []):
        title = item.get("title", "")
        link = item.get("link", "")
        if not title or not link or link in seen:
            continue
        seen.add(link)
        author = item.get("author") or ""
        date = _parse_date(item.get("date") or "")
        desc = item.get("description") or ""
        articles.append({
            "title": title,
            "url": link,
            "date": date,
            "desc": desc,
            "tags": classify_tags(title, desc),
            "meta": {
                "author": author,
            },
        })
    return articles


def collect_github_trending() -> list[dict]:
    """GitHub Trending 日本語まとめ: RSS Feed からトレンドリポジトリを収集
    データソース: https://github.com/yashikota/github-trending-ja
    Copyright (c) 2025 kota - MIT License
    """
    url = "https://yashikota.github.io/github-trending-ja/feed.xml"
    r = get(url)
    if not r:
        return []
    articles = []
    for item in parse_rss(r.content):
        raw_title = item.get("title", "")
        desc = item.get("desc", "")
        # タイトルを「リポ名 - 説明」に分割
        parts = raw_title.split(" - ", 1)
        repo_name = parts[0].strip()
        repo_desc = parts[1].strip() if len(parts) > 1 else ""
        # descriptionからスター数を抽出（例: "スター数: 8,803 (+686)"）
        stars = 0
        m = re.search(r'スター数[:：]\s*([\d,]+)', desc)
        if not m:
            m = re.search(r'⭐\s*([\d,]+)', desc)
        if m:
            stars = int(m.group(1).replace(",", ""))
        # descriptionから言語を抽出（例: "言語: Python<br>"）
        lang = ""
        m_lang = re.search(r'言語[:：]\s*([^<\s]+)', desc)
        if m_lang:
            lang = m_lang.group(1)
        articles.append({
            "title": repo_name,
            "url": item["url"],
            "date": item.get("date", ""),
            "desc": repo_desc,
            "tags": classify_tags(raw_title, desc),
            "meta": {
                "stars": stars,
                "lang": lang,
            },
        })
    articles.sort(key=lambda a: a["meta"].get("stars", 0), reverse=True)
    return articles


# --- Markdown ---
CSS = """<style>
.tag { font-size: 0.72rem; font-weight: 700; padding: 2px 7px; border-radius: 3px; white-space: nowrap; }
.tag-agent { color: #fff; background: #66bb6a; }
.tag-ai    { color: #fff; background: #ffa726; }
.tag-ml    { color: #fff; background: #ab47bc; }
.tag-cv    { color: #fff; background: #42a5f5; }
.tag-research { color: #fff; background: #5c6bc0; }
.tag-sec   { color: #fff; background: #ef5350; }
.tag-poem  { color: #fff; background: #ec407a; }
.tag-eco   { color: #fff; background: #ffca28; }
.tag-front { color: #fff; background: #26c6da; }
.tag-back  { color: #fff; background: #a1887f; }
.tag-cloud { color: #fff; background: #29b6f6; }
.tag-lang  { color: #fff; background: #ce93d8; }
.tag-git   { color: #fff; background: #78909c; }
.tag-pkg   { color: #fff; background: #f06292; }
.tag-oss   { color: #fff; background: #9ccc65; }
.tag-dev   { color: #fff; background: #4db6ac; }
.tag-other { color: #fff; background: #bdbdbd; }
.tab-nav { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 0.5rem; }
.tab-btn { padding: 6px 14px; border: none; border-radius: 20px; cursor: pointer;
  font-size: 0.85rem; font-weight: 700; background: #e8e8e8; color: #444; transition: background 0.15s; }
.tab-btn.active { background: #333; color: #fff; }
.sort-bar { display: flex; gap: 5px; margin-bottom: 1rem; }
.sort-btn { padding: 3px 11px; border: 1px solid #ccc; border-radius: 20px; cursor: pointer;
  font-size: 0.75rem; font-weight: 700; background: #fff; color: #666; transition: all 0.15s; }
.sort-btn.active { background: #333; color: #fff; border-color: #333; }
.tab-pane { display: none; }
.tab-pane.active { display: block; }
.item { padding: 8px 0; border-bottom: 1px solid #eee; }
.item-title { font-size: 0.95rem; font-weight: 600; }
.item-title a, .item-title a:visited { color: #2a7ae2 !important; text-decoration: none; }
.item-title a:hover { text-decoration: underline; }
.item-meta { font-size: 0.78rem; color: #888; margin-top: 2px; }
details { margin-top: 6px; }
details summary { cursor: pointer; font-size: 0.82rem; color: #888; margin-top: 4px; }
details blockquote { font-size: 0.85rem; line-height: 1.6; margin: 8px 0 0 0; padding: 8px 12px; color: #555; border-left: 3px solid #ddd; background: transparent; }
.kindle-footer { margin-top: 2rem; padding: 1rem 0; border-top: 1px solid #eee; }
.kindle-btn { display: inline-block; padding: 8px 20px; background: #fff !important; color: #ff9900 !important;
  border: 2px solid #ff9900; font-size: 0.85rem; font-weight: 700; border-radius: 4px; text-decoration: none !important; }
.kindle-btn:hover { background: #fff8f0 !important; }
</style>"""

TAB_NAV = """<div class="tab-nav">
  <button class="tab-btn active" onclick="switchTab('zenn',this)">Zenn</button>
  <button class="tab-btn" onclick="switchTab('qiita',this)">Qiita</button>
  <button class="tab-btn" onclick="switchTab('hatena',this)">はてな</button>
  <button class="tab-btn" onclick="switchTab('blog',this)">Blog</button>
  <button class="tab-btn" onclick="switchTab('hn',this)">HN</button>
  <button class="tab-btn" onclick="switchTab('slides',this)">Slides</button>
  <button class="tab-btn" onclick="switchTab('ghtrend',this)">Github</button>
</div>
<div class="sort-bar">
  <button id="sort-latest" class="sort-btn active" onclick="setSort('latest',this)">Latest</button>
  <button id="sort-hotness" class="sort-btn" onclick="setSort('hotness',this)">Hotness</button>
</div>"""

SWITCH_JS = """<script>
let _sort = 'latest';

function sortPane(pane, mode) {
  const items = Array.from(pane.querySelectorAll('.item'));
  items.sort((a, b) => mode === 'latest'
    ? (b.dataset.date || '').localeCompare(a.dataset.date || '')
    : parseInt(b.dataset.count || '0') - parseInt(a.dataset.count || '0')
  );
  items.forEach(item => pane.appendChild(item));
}

function setSort(mode, btn) {
  _sort = mode;
  document.querySelectorAll('.sort-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  const active = document.querySelector('.tab-pane.active');
  if (active) sortPane(active, mode);
}

function switchTab(id, btn) {
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  const pane = document.getElementById('tab-' + id);
  pane.classList.add('active');
  btn.classList.add('active');
  const sortBar = document.querySelector('.sort-bar');
  const btnL = document.getElementById('sort-latest');
  const btnH = document.getElementById('sort-hotness');
  if (id === 'slides') {
    sortBar.style.display = '';
    btnL.style.display = ''; btnH.style.display = 'none';
    setSort('latest', btnL);
  } else if (id === 'ghtrend') {
    sortBar.style.display = '';
    btnL.style.display = 'none'; btnH.style.display = '';
    setSort('hotness', btnH);
  } else {
    sortBar.style.display = '';
    btnL.style.display = ''; btnH.style.display = '';
    sortPane(pane, _sort);
  }
}
</script>"""


def _strip_utm(url: str) -> str:
    """URLからUTMパラメータを除去して正規化"""
    if "?" not in url:
        return url
    base, _, qs = url.partition("?")
    params = [p for p in qs.split("&") if not p.startswith("utm_")]
    return f"{base}?{'&'.join(params)}" if params else base


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def safe_href(url: str) -> str:
    """URLスキームを検証し、http/https以外は '#' に置換してXSSを防止"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "#"
    return esc(url)


def render_standard(articles: list[dict], tab_id: str, count_icon: str, count_key: str,
                    source_url: str = "", source_credit: str = "") -> list[str]:
    active = " active" if tab_id == "zenn" else ""
    # デフォルト: 最新順
    sorted_articles = sorted(articles, key=lambda a: a.get("date", ""), reverse=True)
    lines = [f'<div id="tab-{tab_id}" class="tab-pane{active}">']
    for a in sorted_articles:
        title  = esc(a["title"])
        url    = safe_href(a["url"])
        date   = a.get("date", "")[5:10]
        count  = a["meta"].get(count_key, 0)
        author = esc(a["meta"].get("author", ""))
        ts     = tag_spans(a.get("tags", ["dev"]))
        count_str  = f"{count_icon} {count}" if (count_icon and count) else ""
        author_str = f"@{author}" if author else ""
        meta_parts = [p for p in [date, count_str, author_str] if p] + [ts]
        lines += [
            f'<div class="item" data-date="{a.get("date","")}" data-count="{count}">',
            f'  <div class="item-title"><a href="{url}">{title}</a></div>',
            f'  <div class="item-meta">{" &nbsp; ".join(meta_parts)}</div>',
            "</div>",
        ]
    if source_url:
        credit_line = f'<div class="item-meta" style="margin-top:12px;">引用: <a href="{safe_href(source_url)}" target="_blank" rel="noopener">{esc(source_url)}</a>'
        if source_credit:
            credit_line += f'<br>{esc(source_credit)}'
        credit_line += '</div>'
        lines.append(credit_line)
    lines.append("</div>")
    return lines


def render_hn(articles: list[dict]) -> list[str]:
    # デフォルト: 最新順
    sorted_articles = sorted(articles, key=lambda a: a.get("date", ""), reverse=True)
    lines = ['<div id="tab-hn" class="tab-pane">']
    for a in sorted_articles:
        title    = esc(a["title"])
        title_ja = esc(a["meta"].get("title_ja", ""))
        url      = safe_href(a["url"])
        hn_url   = safe_href(a["meta"].get("hn_url", ""))
        pts      = a["meta"].get("points", 0)
        cmts     = a["meta"].get("comments", 0)
        date     = a.get("date", "")[5:10]
        ts       = tag_spans(a.get("tags", ["dev"]))
        meta_parts = [p for p in [date, f"🔥 {pts}" if pts else "", f"💬 {cmts}" if cmts else ""] if p] + [ts]
        lines += [
            f'<div class="item" data-date="{a.get("date","")}" data-count="{pts}">',
            f'  <div class="item-title"><a href="{url}">{title}</a></div>',
            f'  <div class="item-meta">{" &nbsp; ".join(meta_parts)}</div>',
        ]
        if title_ja:
            lines += [
                "  <details>",
                f'    <summary>タイトルの日本語訳</summary>',
                f'    <blockquote>{title_ja}</blockquote>',
                "  </details>",
            ]
        lines.append("</div>")
    lines.append("</div>")
    return lines


def render_ghtrend(articles: list[dict], source_url: str = "", source_credit: str = "") -> list[str]:
    """GitHub Trending専用レンダラー: リポ名リンク + 説明文 + スター数 + 言語"""
    lines = ['<div id="tab-ghtrend" class="tab-pane">']
    for a in articles:
        repo   = esc(a["title"])
        url    = safe_href(a["url"])
        desc   = esc(a.get("desc", ""))
        date   = a.get("date", "")[5:10]
        stars  = a["meta"].get("stars", 0)
        lang   = esc(a["meta"].get("lang", ""))
        ts     = tag_spans(a.get("tags", ["dev"]))
        stars_str = f"⭐ {stars:,}" if stars else ""
        meta_parts = [p for p in [date, stars_str, lang] if p] + [ts]
        lines += [
            f'<div class="item" data-date="{a.get("date","")}" data-count="{stars}">',
            f'  <div class="item-title"><a href="{url}">{repo}</a></div>',
        ]
        if desc:
            lines.append(f'  <div class="item-meta">{desc}</div>')
        lines += [
            f'  <div class="item-meta">{" &nbsp; ".join(meta_parts)}</div>',
            "</div>",
        ]
    if source_url:
        credit_line = f'<div class="item-meta" style="margin-top:12px;">引用: <a href="{safe_href(source_url)}" target="_blank" rel="noopener">{esc(source_url)}</a>'
        if source_credit:
            credit_line += f'<br>{esc(source_credit)}'
        credit_line += '</div>'
        lines.append(credit_line)
    lines.append("</div>")
    return lines


def main():
    now = datetime.now(JST)
    date_label = now.strftime("%Y-%m-%d")
    time_label = now.strftime("%H:%M")
    timestamp  = now.strftime("%Y-%m-%d-%H-%M")

    print("収集開始...")

    with ThreadPoolExecutor(max_workers=7) as ex:
        f_zenn     = ex.submit(collect_zenn)
        f_qiita    = ex.submit(collect_qiita)
        f_hatena   = ex.submit(collect_hatena)
        f_blog     = ex.submit(collect_hatena_blog)
        f_hn       = ex.submit(collect_hn)
        f_slides   = ex.submit(collect_slides)
        f_ghtrend  = ex.submit(collect_github_trending)

    zenn_articles    = f_zenn.result()
    qiita_articles   = f_qiita.result()
    hatena_articles  = f_hatena.result()
    blog_articles    = f_blog.result()
    hn_articles      = f_hn.result()
    slides_articles  = f_slides.result()
    ghtrend_articles = f_ghtrend.result()

    print(f"  Zenn: {len(zenn_articles)}, Qiita: {len(qiita_articles)}, "
          f"はてな: {len(hatena_articles)}, Blog: {len(blog_articles)}, "
          f"HN: {len(hn_articles)}, Slides: {len(slides_articles)}, "
          f"GH Trending: {len(ghtrend_articles)}")

    # --- 日付フィルタ（Zennはトレンドなのでフィルタなし、他は14日）---
    cutoff = (now - timedelta(days=14)).strftime("%Y-%m-%d")
    def recent(arts: list[dict]) -> list[dict]:
        """date が cutoff 以降の記事のみ残す。日付なし記事は保持。"""
        return [a for a in arts if not a.get("date") or a["date"][:10] >= cutoff]
    # Zennはorder=weeklyでその日のトレンド取得済みなのでフィルタ不要
    qiita_articles  = recent(qiita_articles)
    hatena_articles = recent(hatena_articles)
    slides_articles = recent(slides_articles)
    # blogは1ヶ月の検索RSSなのでフィルタ不要
    # GH Trendingは当日のトレンドなのでフィルタ不要
    print(f"  日付フィルタ後 → Zenn: {len(zenn_articles)}(フィルタなし), Qiita: {len(qiita_articles)}, "
          f"はてな: {len(hatena_articles)}, Blog: {len(blog_articles)}, "
          f"Slides: {len(slides_articles)}, GH Trending: {len(ghtrend_articles)}")

    # --- タブ間の重複排除（先のタブを優先、UTMパラメータ除去して比較）---
    global_seen: set[str] = set()
    def dedup(arts: list[dict]) -> list[dict]:
        result = []
        for a in arts:
            canonical = _strip_utm(a["url"])
            if canonical not in global_seen:
                global_seen.add(canonical)
                result.append(a)
        return result
    zenn_articles   = dedup(zenn_articles)
    qiita_articles  = dedup(qiita_articles)
    hatena_articles = dedup(hatena_articles)
    blog_articles   = dedup(blog_articles)
    hn_articles     = dedup(hn_articles)
    slides_articles  = dedup(slides_articles)
    ghtrend_articles = dedup(ghtrend_articles)
    print(f"  重複排除後 → Zenn: {len(zenn_articles)}, Qiita: {len(qiita_articles)}, "
          f"はてな: {len(hatena_articles)}, Blog: {len(blog_articles)}, "
          f"HN: {len(hn_articles)}, Slides: {len(slides_articles)}, "
          f"GH Trending: {len(ghtrend_articles)}")

    lines: list[str] = [
        "---",
        "layout: post",
        f'title: "{date_label} : ニュース"',
        f"date: {date_label} {time_label}:00 +0900",
        "categories: [daily]",
        "---",
        "",
        CSS,
        "",
        TAB_NAV,
        "",
    ]

    lines += render_standard(zenn_articles,   "zenn",   "❤️",  "likes")
    lines += [""]
    lines += render_standard(qiita_articles,  "qiita",  "👍",  "likes")
    lines += [""]
    lines += render_standard(hatena_articles, "hatena", "🔖", "bookmarks")
    lines += [""]
    lines += render_standard(blog_articles,   "blog",  "🔖", "bookmarks")
    lines += [""]
    lines += render_hn(hn_articles)
    lines += [""]
    lines += render_standard(slides_articles, "slides", "", "",
                             source_url="https://yuji.software/tech_slideshare/",
                             source_credit="by YujiSoftware/tech_slideshare")
    lines += [""]
    lines += render_ghtrend(ghtrend_articles,
                            source_url="https://github-trending-ja.yashikota.com/",
                            source_credit="Copyright (c) 2025 kota - MIT License")
    lines += [
        "",
        f'<div class="kindle-footer"><a class="kindle-btn" href="{KINDLE_DAILY_URL}" target="_blank" rel="noopener">Kindle 日替わりセール</a></div>',
        "",
        SWITCH_JS,
        "",
    ]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{timestamp}-neta-trend.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"保存: {out_path}")
    if deepl_chars_used:
        print(f"  DeepL消費文字数: {deepl_chars_used:,} 文字")

    # Slack通知用URLをファイルに書き出す
    date_path = now.strftime("%Y/%m/%d")
    time_slug = now.strftime("%H-%M")
    site_url = os.environ.get("SITE_URL", "https://canon-so8.github.io/news-claw")
    page_url = f"{site_url}/daily/{date_path}/{time_slug}-neta-trend/"
    try:
        Path("/tmp/daily_url.txt").write_text(page_url)
    except Exception:
        pass
    return str(out_path)


if __name__ == "__main__":
    main()
