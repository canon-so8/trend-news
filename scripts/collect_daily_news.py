#!/usr/bin/env python3
"""
デイリーニュース収集スクリプト
Claude Code 不要 - RSS/API から直接データ取得してJekyll Markdownを生成

ソース:
  - Zenn   : API (liked_count付き)
  - Qiita  : API v2 (likes_count付き)
  - はてな  : RSS/RDF (bookmarkcount付き)
  - HN     : Algolia API + Google Translate（タイトル日本語訳）
  - xTech  : RSS 1.0/RDF
"""
import json
import os
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

JST = timezone(timedelta(hours=9))
REPO_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = REPO_ROOT / "_posts" / "daily_news"

# RSS 名前空間
RSS1   = "http://purl.org/rss/1.0/"
DC     = "http://purl.org/dc/elements/1.1/"
ATOM   = "http://www.w3.org/2005/Atom"
RDF    = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
HATENA = "http://www.hatena.ne.jp/info/xmlns#"

# --- タグ判定（優先順位: AI > ML > CV > POEM > ECO > DEV > other）---
AI_KEYWORDS   = [
    "llm", "agent", "agentic", "gpt", "claude", "gemini", "openai", "anthropic",
    "chatgpt", "生成ai", "大規模言語", "プロンプト", "rag", "chain-of-thought",
    "reasoning", "fine-tun", "ファインチューニング", "alignment", "ai agent",
    "mcp ", "claude code", "copilot", "cursor ", "人工知能", "生成モデル",
]
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
    "speech synthesis", "音声モデル",
]
CV_KEYWORDS   = [
    "image", "video", "vision", "diffusion", "gan", "vae", "3d reconstruction",
    "yolo", "resnet", "vit ", "pose estimation", "depth estimation",
    "画像認識", "画像生成", "動画生成", "物体検出", "セグメンテーション", "ocr",
    "コンピュータビジョン", "点群", "自動運転", "autonomous driving",
    "ロボット", "robot", "lerobot", "3次元",
]
POEM_KEYWORDS = [
    "キャリア", "エンジニア哲学", "転職", "仕事術", "思想", "ポエム",
    "生き方", "働き方", "マインド", "組織論", "チームビルディング",
    "エンジニアリング文化", "リーダーシップ", "マネジメント論", "culture",
    "philosophy", "エンジニアとして", "技術者として", "プログラマとして",
]
ECO_KEYWORDS  = [
    "経済", "半導体", "nvidia", "tsmc", "テック企業", "産業動向", "規制",
    "政策", "株価", "business", "startup", "スタートアップ", "vc ", "融資",
    "ipo", "資金調達", "市場規模", "シェア", "競合", "買収", "合併",
    "apple", "google", "microsoft", "meta ", "amazon", "tesla",
]
DEV_KEYWORDS  = [
    "python", "javascript", "typescript", "rust", "go ", "java ", "kotlin",
    "swift", "c++", "c#", "kubernetes", "docker", "linux", "cli", "sdk", "vscode",
    "ios開発", "android開発", "アプリ開発", "ios向け",
    "プログラミング", "コーディング", "ライブラリ", "フレームワーク",
    "npm", "pip ", "パッケージ", "依存関係", "git ", "github",
    "デプロイ", "インフラ", "クラウド", "aws", "gcp", "azure",
    "セキュリティ", "脆弱性", "サプライチェーン", "暗号化", "認証",
    "データベース", "sql", "postgresql", "redis", "mongodb",
    "バグ", "テスト", "ci/cd", "コマンドライン", "ターミナル", "シェルスクリプト",
    "bash ", "zsh", "makefile", "api設計", "マイクロサービス", "サーバーレス",
    "全文検索", "uuid", "スキーマ", "orm ", "migration", "パフォーマンス",
    "リファクタリング", "コードレビュー", "開発環境", "wsl", "homebrew",
    "ログ設計", "ログ収集", "監視", "オブザーバビリティ", "rest api", "restapi",
    "テーブル設計", "スキーマ設計", "hostsファイル", "crowdstrike",
    "ssh", "ssl", "tls", "xss", "csrf", "ペネトレーション",
]

TAG_LABELS = {
    "ai":    ("AI",    "tag-ai"),
    "ml":    ("ML",    "tag-ml"),
    "cv":    ("CV",    "tag-cv"),
    "poem":  ("ポエム", "tag-poem"),
    "eco":   ("経済",  "tag-eco"),
    "dev":   ("Dev",   "tag-dev"),
    "other": ("Other", "tag-other"),
}


# "ai" を単体でマッチ（"api","mail","detail"などへの誤検知を防ぐため正規表現）
_AI_SOLO_RE = re.compile(r'(?<![a-z])ai(?![a-z])')


def classify_tag(title: str, desc: str = "") -> str:
    text = (title + " " + desc).lower()
    if any(k in text for k in AI_KEYWORDS):   return "ai"
    if _AI_SOLO_RE.search(text):               return "ai"
    if any(k in text for k in ML_KEYWORDS):   return "ml"
    if any(k in text for k in CV_KEYWORDS):   return "cv"
    if any(k in text for k in POEM_KEYWORDS): return "poem"
    if any(k in text for k in ECO_KEYWORDS):  return "eco"
    if any(k in text for k in DEV_KEYWORDS):  return "dev"
    return "other"


def tag_span(tag_key: str) -> str:
    label, cls = TAG_LABELS.get(tag_key, ("Other", "tag-other"))
    return f'<span class="tag {cls}">{label}</span>'


# --- HTTP セッション ---
def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"})
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
        pub    = _text(pub_el)[:16]
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


def translate_google(text: str) -> str:
    """Google Translate 非公式 API（フォールバック用）"""
    if not text:
        return ""
    url = (
        "https://translate.googleapis.com/translate_a/single"
        f"?client=gtx&sl=en&tl=ja&dt=t&q={urllib.parse.quote(text)}"
    )
    r = get(url, timeout=10)
    if not r:
        return ""
    try:
        data = r.json()
        return "".join(chunk[0] for chunk in data[0] if chunk[0])
    except Exception:
        return ""


def translate_ja(text: str) -> str:
    """DeepL優先、フォールバックでGoogle Translate"""
    result = translate_deepl(text)
    if result:
        return result
    return translate_google(text)


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
    """Zenn API: ウィークリートレンド（explore相当）で取得してからいいね数でソート"""
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

    # Techs + Ideas（ウィークリートレンド = explore ページ相当）
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
        a["tag"] = classify_tag(a["title"])
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
            if item["url"] and item["url"] not in seen:
                seen.add(item["url"])
                item["tag"] = classify_tag(item["title"], item["desc"])
                item["meta"].setdefault("likes", 0)
                item["meta"].setdefault("author", "")
                trend_articles.append(item)

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
                    "tag": classify_tag(title),
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

    # トレンド順を維持（フィード順）+ タグ別をいいね数順で後ろに追加
    tag_articles.sort(key=lambda a: a["meta"].get("likes", 0), reverse=True)
    return (trend_articles + tag_articles)[:40]


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
                    item["tag"] = classify_tag(item["title"], item["desc"])
                    articles.append(item)

    articles.sort(key=lambda a: a["meta"].get("bookmarks", 0), reverse=True)
    return articles[:30]


def collect_hn() -> list[dict]:
    """Algolia API + Google Translate でタイトルを日本語訳（当日のみ）"""
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
            "tag": classify_tag(title),
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


def collect_nikkei() -> list[dict]:
    """日経xTech + 日経テクノロジー RSS（直近3日）"""
    cutoff = (datetime.now(JST) - timedelta(days=3)).strftime("%Y-%m-%d")
    urls = [
        "https://xtech.nikkei.com/rss/index.rdf",
        "https://www.nikkei.com/news/category/rss/technology/",
    ]
    seen: set[str] = set()
    articles: list[dict] = []
    for url in urls:
        r = get(url)
        if not r:
            continue
        for item in parse_rss(r.content):
            if item["url"] not in seen:
                seen.add(item["url"])
                # 直近3日の記事のみ（日付なしは含める）
                if item.get("date") and item["date"][:10] < cutoff:
                    continue
                tag = classify_tag(item["title"], item["desc"])
                if tag in ("ai", "ml", "cv", "eco", "dev"):
                    item["tag"] = tag
                    articles.append(item)
    return articles[:20]


# --- Markdown ---
CSS = """<style>
.tag { font-size: 0.72rem; font-weight: 700; padding: 2px 7px; border-radius: 3px; white-space: nowrap; }
.tag-ai   { color: #bf5a00; background: #fff3e0; }
.tag-ml   { color: #6a1b9a; background: #f3e5f5; }
.tag-cv   { color: #1a6bbf; background: #e8f0fb; }
.tag-poem { color: #c2185b; background: #fce4ec; }
.tag-eco  { color: #00695c; background: #e0f2f1; }
.tag-dev  { color: #558b2f; background: #f1f8e9; }
.tag-other { color: #666; background: #f2f2f2; }
.tab-nav { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 1rem; }
.tab-btn { padding: 6px 14px; border: none; border-radius: 20px; cursor: pointer;
  font-size: 0.85rem; font-weight: 700; background: #e8e8e8; color: #444; transition: background 0.15s; }
.tab-btn.active { background: #333; color: #fff; }
.tab-pane { display: none; }
.tab-pane.active { display: block; }
.item { padding: 8px 0; border-bottom: 1px solid #eee; }
.item-title { font-size: 0.95rem; font-weight: 600; }
.item-meta { font-size: 0.78rem; color: #888; margin-top: 2px; }
details { margin-top: 6px; }
details summary { cursor: pointer; font-size: 0.82rem; color: #888; margin-top: 4px; }
details blockquote { font-size: 0.85rem; line-height: 1.6; margin: 8px 0 0 0; padding: 8px 12px; color: #555; border-left: 3px solid #ddd; background: transparent; }
</style>"""

TAB_NAV = """<div class="tab-nav">
  <button class="tab-btn active" onclick="switchTab('zenn',this)">Zenn</button>
  <button class="tab-btn" onclick="switchTab('qiita',this)">Qiita</button>
  <button class="tab-btn" onclick="switchTab('hatena',this)">はてな</button>
  <button class="tab-btn" onclick="switchTab('nikkei',this)">日経</button>
  <button class="tab-btn" onclick="switchTab('hn',this)">HN</button>
</div>"""

SWITCH_JS = """<script>
function switchTab(id, btn) {
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + id).classList.add('active');
  btn.classList.add('active');
}
</script>"""


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def render_standard(articles: list[dict], tab_id: str, count_icon: str, count_key: str) -> list[str]:
    active = " active" if tab_id == "zenn" else ""
    lines = [f'<div id="tab-{tab_id}" class="tab-pane{active}">']
    for a in articles:
        title  = esc(a["title"])
        url    = a["url"]
        date   = a.get("date", "")[5:10]
        count  = a["meta"].get(count_key, 0)
        author = esc(a["meta"].get("author", ""))
        ts     = tag_span(a.get("tag", "other"))
        count_str  = f"{count_icon} {count}" if (count_icon and count) else ""
        author_str = f"@{author}" if author else ""
        meta_parts = [p for p in [date, count_str, author_str] if p] + [ts]
        lines += [
            '<div class="item">',
            f'  <div class="item-title"><a href="{url}">{title}</a></div>',
            f'  <div class="item-meta">{" &nbsp; ".join(meta_parts)}</div>',
            "</div>",
        ]
    lines.append("</div>")
    return lines


def render_hn(articles: list[dict]) -> list[str]:
    lines = ['<div id="tab-hn" class="tab-pane">']
    for a in articles:
        title    = esc(a["title"])
        title_ja = esc(a["meta"].get("title_ja", ""))
        url      = a["url"]
        hn_url   = a["meta"].get("hn_url", "")
        pts      = a["meta"].get("points", 0)
        cmts     = a["meta"].get("comments", 0)
        date     = a.get("date", "")[5:10]
        ts       = tag_span(a.get("tag", "other"))
        meta_parts = [p for p in [date, f"🔥 {pts}" if pts else "", f"💬 {cmts}" if cmts else ""] if p] + [ts]
        lines += [
            '<div class="item">',
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


def main():
    now = datetime.now(JST)
    date_label = now.strftime("%Y-%m-%d")
    time_label = now.strftime("%H:%M")
    timestamp  = now.strftime("%Y-%m-%d-%H-%M")

    print("収集開始...")

    with ThreadPoolExecutor(max_workers=5) as ex:
        f_zenn   = ex.submit(collect_zenn)
        f_qiita  = ex.submit(collect_qiita)
        f_hatena = ex.submit(collect_hatena)
        f_hn     = ex.submit(collect_hn)
        f_nikkei = ex.submit(collect_nikkei)

    zenn_articles   = f_zenn.result()
    qiita_articles  = f_qiita.result()
    hatena_articles = f_hatena.result()
    hn_articles     = f_hn.result()
    nikkei_articles = f_nikkei.result()

    print(f"  Zenn: {len(zenn_articles)}, Qiita: {len(qiita_articles)}, "
          f"はてな: {len(hatena_articles)}, HN: {len(hn_articles)}, 日経: {len(nikkei_articles)}")

    # --- 日付フィルタ（Zennはトレンドなのでフィルタなし、他は14日）---
    cutoff = (now - timedelta(days=14)).strftime("%Y-%m-%d")
    def recent(arts: list[dict]) -> list[dict]:
        """date が cutoff 以降の記事のみ残す。日付なし記事は保持。"""
        return [a for a in arts if not a.get("date") or a["date"][:10] >= cutoff]
    # Zennはorder=dailyでトレンド取得済みなのでフィルタ不要
    qiita_articles  = recent(qiita_articles)
    hatena_articles = recent(hatena_articles)
    nikkei_articles = recent(nikkei_articles)
    print(f"  日付フィルタ後 → Zenn: {len(zenn_articles)}(フィルタなし), Qiita: {len(qiita_articles)}, "
          f"はてな: {len(hatena_articles)}, 日経: {len(nikkei_articles)}")

    # --- タブ間の重複排除（先のタブを優先）---
    global_seen: set[str] = set()
    def dedup(arts: list[dict]) -> list[dict]:
        result = []
        for a in arts:
            if a["url"] not in global_seen:
                global_seen.add(a["url"])
                result.append(a)
        return result
    zenn_articles   = dedup(zenn_articles)
    qiita_articles  = dedup(qiita_articles)
    hatena_articles = dedup(hatena_articles)
    nikkei_articles = dedup(nikkei_articles)
    hn_articles     = dedup(hn_articles)
    print(f"  重複排除後 → Zenn: {len(zenn_articles)}, Qiita: {len(qiita_articles)}, "
          f"はてな: {len(hatena_articles)}, 日経: {len(nikkei_articles)}, HN: {len(hn_articles)}")

    lines: list[str] = [
        "---",
        "layout: post",
        f'title: "{date_label}:Zenn・Qiita・はてブ・日経・HN"',
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
    lines += render_standard(nikkei_articles, "nikkei", "",   "")
    lines += [""]
    lines += render_hn(hn_articles)
    lines += ["", SWITCH_JS, ""]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{timestamp}-neta-trend.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"保存: {out_path}")
    if deepl_chars_used:
        print(f"  DeepL消費文字数: {deepl_chars_used:,} 文字")

    # Slack通知用URLをファイルに書き出す
    date_path = now.strftime("%Y/%m/%d")
    time_slug = now.strftime("%H-%M")
    page_url = f"https://canon-so8.github.io/trend-news/daily/{date_path}/{time_slug}-neta-trend/"
    try:
        Path("/tmp/daily_url.txt").write_text(page_url)
    except Exception:
        pass
    return str(out_path)


if __name__ == "__main__":
    main()
