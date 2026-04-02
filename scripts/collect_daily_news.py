#!/usr/bin/env python3
"""
デイリーニュース収集スクリプト
Claude Code 不要 - RSS/API から直接データ取得してJekyll Markdownを生成

ソース:
  - Zenn  : RSS (topicごと)
  - Qiita : RSS (tagごと)
  - はてな : RSS (hotentry)
  - HN    : Algolia API
  - Nikkei: RSS
"""
import json
import os
import sys
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

JST = timezone(timedelta(hours=9))
REPO_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = REPO_ROOT / "_posts" / "daily_news"

# Atom/RSS 名前空間
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
}

# --- タグ判定 ---
AI_KEYWORDS = ["ai", "llm", "agent", "gpt", "claude", "gemini", "openai", "anthropic",
               "chatgpt", "機械学習", "深層学習", "人工知能", "生成ai", "大規模言語"]
ML_KEYWORDS = ["machine learning", "deep learning", "reinforcement", "pytorch", "tensorflow",
               "kaggle", "neural", "モデル学習", "ファインチューニング", "統計", "回帰", "分類"]
CV_KEYWORDS = ["image", "video", "vision", "diffusion", "画像", "動画", "映像", "3d", "点群"]
POEM_KEYWORDS = ["キャリア", "エンジニア哲学", "転職", "仕事術", "思想", "ポエム", "考え方",
                 "生き方", "働き方", "マインド", "culture", "philosophy"]
ECO_KEYWORDS = ["経済", "半導体", "nvidia", "tsmc", "テック企業", "産業", "規制", "政策",
                "株価", "business", "startup", "vc", "融資", "ipo"]
DEV_KEYWORDS = ["python", "javascript", "typescript", "rust", "go ", "java", "kubernetes",
                "docker", "linux", "cli", "api", "sdk", "vscode", "開発ツール", "プログラミング"]


def classify_tag(title: str, desc: str = "") -> str:
    text = (title + " " + desc).lower()
    if any(k in text for k in AI_KEYWORDS):
        return "ai"
    if any(k in text for k in ML_KEYWORDS):
        return "ml"
    if any(k in text for k in CV_KEYWORDS):
        return "cv"
    if any(k in text for k in POEM_KEYWORDS):
        return "poem"
    if any(k in text for k in ECO_KEYWORDS):
        return "eco"
    if any(k in text for k in DEV_KEYWORDS):
        return "dev"
    return "other"


TAG_LABELS = {
    "ai": ("AI", "tag-ai"),
    "ml": ("ML", "tag-ml"),
    "cv": ("CV", "tag-cv"),
    "poem": ("ポエム", "tag-poem"),
    "eco": ("経済", "tag-eco"),
    "dev": ("Dev", "tag-dev"),
    "other": ("Other", "tag-other"),
}


def tag_span(tag_key: str) -> str:
    label, cls = TAG_LABELS.get(tag_key, ("Other", "tag-other"))
    return f'<span class="tag {cls}">{label}</span>'


# --- HTTP ユーティリティ ---
def fetch_url(url: str, timeout: int = 20) -> Optional[bytes]:
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; TrendBot/1.0)"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as e:
        print(f"  fetch error [{url[:60]}]: {e}", file=sys.stderr)
        return None


# RSS 名前空間
RSS1 = "http://purl.org/rss/1.0/"
DC = "http://purl.org/dc/elements/1.1/"
ATOM = "http://www.w3.org/2005/Atom"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"


def _text(el) -> str:
    return (el.text or "").strip() if el is not None else ""


# --- RSS パーサー ---
def parse_rss(data: bytes) -> list[dict]:
    """RSS 1.0(RDF) / RSS 2.0 / Atom フィードを解析して記事リストを返す"""
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

    ns = root.tag  # 例: "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF"

    # --- Atom ---
    if f"{{{ATOM}}}feed" in ns or root.tag == f"{{{ATOM}}}feed":
        for entry in root.findall(f"{{{ATOM}}}entry"):
            title_el = entry.find(f"{{{ATOM}}}title")
            link_el = entry.find(f"{{{ATOM}}}link")
            _pub = entry.find(f"{{{ATOM}}}published")
            pub_el = _pub if _pub is not None else entry.find(f"{{{ATOM}}}updated")
            _sum = entry.find(f"{{{ATOM}}}summary")
            summary_el = _sum if _sum is not None else entry.find(f"{{{ATOM}}}content")
            title = _text(title_el)
            url = ""
            if link_el is not None:
                url = link_el.get("href") or _text(link_el)
            pub = _text(pub_el)[:10]
            desc = _text(summary_el)
            if title and url:
                items.append({"title": title, "url": url, "date": pub, "desc": desc, "meta": {}})
        return items

    # --- RSS 1.0 (RDF) ---
    if f"{{{RDF}}}RDF" in ns or root.tag == f"{{{RDF}}}RDF":
        for item in root.findall(f"{{{RSS1}}}item"):
            title = _text(item.find(f"{{{RSS1}}}title"))
            url = _text(item.find(f"{{{RSS1}}}link"))
            pub = _text(item.find(f"{{{DC}}}date"))[:10]
            desc = _text(item.find(f"{{{RSS1}}}description"))
            if title and url:
                items.append({"title": title, "url": url, "date": pub, "desc": desc, "meta": {}})
        return items

    # --- RSS 2.0 ---
    channel = root.find("channel")
    if channel is None:
        channel = root
    for item in channel.findall("item"):
        title = _text(item.find("title"))
        url = _text(item.find("link"))
        _pd = item.find("pubDate")
        pub_el = _pd if _pd is not None else item.find(f"{{{DC}}}date")
        pub = _text(pub_el)[:16]
        desc = _text(item.find("description"))
        if title and url:
            items.append({"title": title, "url": url, "date": pub, "desc": desc, "meta": {}})

    return items


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
    # Zennのトピック別RSSは /topics/{topic}/feed 形式
    urls = [f"https://zenn.dev/topics/{t}/feed" for t in ZENN_TOPICS]
    urls.insert(0, "https://zenn.dev/feed")

    seen = set()
    articles = []

    def fetch_one(url):
        data = fetch_url(url)
        return parse_rss(data) if data else []

    with ThreadPoolExecutor(max_workers=len(urls)) as ex:
        for items in ex.map(fetch_one, urls):
            for item in items:
                if item["url"] not in seen:
                    seen.add(item["url"])
                    tag = classify_tag(item["title"], item["desc"])
                    item["tag"] = tag
                    articles.append(item)

    return articles[:40]


def collect_qiita() -> list[dict]:
    urls = [f"https://qiita.com/tags/{urllib.parse.quote(t)}/feed" for t in QIITA_TAGS]
    urls.insert(0, "https://qiita.com/popular-items/feed")

    seen = set()
    articles = []

    def fetch_one(url):
        data = fetch_url(url)
        return parse_rss(data) if data else []

    with ThreadPoolExecutor(max_workers=len(urls)) as ex:
        for items in ex.map(fetch_one, urls):
            for item in items:
                if item["url"] not in seen:
                    seen.add(item["url"])
                    tag = classify_tag(item["title"], item["desc"])
                    item["tag"] = tag
                    articles.append(item)

    return articles[:40]


def collect_hatena() -> list[dict]:
    # はてなブックマーク: ホットエントリRSS + キーワード検索RSS (RSS1.0/RDF形式)
    urls = [
        "https://b.hatena.ne.jp/hotentry/it.rss",
        "https://b.hatena.ne.jp/q/AI?date_range=1w&sort=hot&mode=rss&safe=on&target=entry&users=3",
        "https://b.hatena.ne.jp/q/%E6%A9%9F%E6%A2%B0%E5%AD%A6%E7%BF%92?date_range=1w&sort=hot&mode=rss&safe=on&target=entry&users=3",
        "https://b.hatena.ne.jp/q/%E3%83%97%E3%83%AD%E3%82%B0%E3%83%A9%E3%83%9F%E3%83%B3%E3%82%B0?date_range=1w&sort=hot&mode=rss&safe=on&target=entry&users=3",
    ]
    seen = set()
    articles = []

    def fetch_one(url):
        data = fetch_url(url)
        return parse_rss(data) if data else []

    with ThreadPoolExecutor(max_workers=3) as ex:
        for items in ex.map(fetch_one, urls):
            for item in items:
                if item["url"] not in seen:
                    seen.add(item["url"])
                    tag = classify_tag(item["title"], item["desc"])
                    item["tag"] = tag
                    articles.append(item)

    return articles[:30]


def collect_hn() -> list[dict]:
    """Algolia API で HN のスコア100以上 or コメント50以上の記事を取得"""
    url = "https://hn.algolia.com/api/v1/search?tags=story&numericFilters=points%3E80&hitsPerPage=30&attributesToRetrieve=title,url,points,num_comments,created_at"
    data = fetch_url(url)
    if not data:
        return []
    try:
        hits = json.loads(data).get("hits", [])
    except Exception:
        return []
    articles = []
    for h in hits:
        title = h.get("title", "")
        story_url = h.get("url", "")
        if not title or not story_url:
            continue
        pts = h.get("points", 0)
        cmts = h.get("num_comments", 0)
        date = (h.get("created_at") or "")[:10]
        tag = classify_tag(title)
        articles.append({
            "title": title,
            "url": story_url,
            "date": date,
            "desc": "",
            "tag": tag,
            "meta": {"points": pts, "comments": cmts},
        })
    return articles


def collect_nikkei() -> list[dict]:
    # 日経xTech RSS (RSS 1.0/RDF形式) + ITmedia AI+
    urls = [
        "https://xtech.nikkei.com/rss/index.rdf",
        "https://rss.itmedia.co.jp/rss/2.0/aiplus.xml",
    ]
    seen = set()
    articles = []
    for url in urls:
        data = fetch_url(url)
        if not data:
            continue
        for item in parse_rss(data):
            if item["url"] not in seen:
                seen.add(item["url"])
                tag = classify_tag(item["title"], item["desc"])
                # 興味領域のみ残す
                if tag in ("ai", "ml", "cv", "eco", "dev"):
                    item["tag"] = tag
                    articles.append(item)
    return articles[:20]


# --- Markdownレンダリング ---
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


def render_items(articles: list[dict], tab_id: str, extra_meta_fn=None) -> list[str]:
    lines = [f'<div id="tab-{tab_id}" class="tab-pane{" active" if tab_id == "zenn" else ""}">']
    for a in articles:
        title = a["title"].replace('"', '&quot;').replace("<", "&lt;").replace(">", "&gt;")
        url = a["url"]
        date = a["date"][:7] if len(a.get("date", "")) >= 7 else a.get("date", "")
        ts = tag_span(a.get("tag", "other"))
        meta_parts = []
        if date:
            meta_parts.append(date)
        if extra_meta_fn:
            meta_parts += extra_meta_fn(a)
        meta_parts.append(ts)
        meta_str = " &nbsp; ".join(meta_parts)
        lines += [
            '<div class="item">',
            f'  <div class="item-title"><a href="{url}">{title}</a></div>',
            f'  <div class="item-meta">{meta_str}</div>',
            "</div>",
        ]
    lines.append("</div>")
    return lines


def main():
    now = datetime.now(JST)
    date_label = now.strftime("%Y-%m-%d")
    time_label = now.strftime("%H:%M")
    timestamp = now.strftime("%Y-%m-%d-%H-%M")

    print("収集開始...")

    # 並列収集
    with ThreadPoolExecutor(max_workers=5) as ex:
        f_zenn = ex.submit(collect_zenn)
        f_qiita = ex.submit(collect_qiita)
        f_hatena = ex.submit(collect_hatena)
        f_hn = ex.submit(collect_hn)
        f_nikkei = ex.submit(collect_nikkei)

    zenn_articles = f_zenn.result()
    qiita_articles = f_qiita.result()
    hatena_articles = f_hatena.result()
    hn_articles = f_hn.result()
    nikkei_articles = f_nikkei.result()

    print(f"  Zenn: {len(zenn_articles)}, Qiita: {len(qiita_articles)}, "
          f"はてな: {len(hatena_articles)}, HN: {len(hn_articles)}, 日経: {len(nikkei_articles)}")

    # Markdown構築
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

    lines += render_items(zenn_articles, "zenn")
    lines += [""]
    lines += render_items(qiita_articles, "qiita")
    lines += [""]
    lines += render_items(hatena_articles, "hatena")
    lines += [""]
    lines += render_items(nikkei_articles, "nikkei")
    lines += [""]

    # HN は points/comments を追加
    def hn_meta(a):
        pts = a["meta"].get("points", 0)
        cmts = a["meta"].get("comments", 0)
        parts = []
        if pts:
            parts.append(f"🔥 {pts} pts")
        if cmts:
            parts.append(f"💬 {cmts}")
        return parts

    lines += render_items(hn_articles, "hn", extra_meta_fn=hn_meta)
    lines += ["", SWITCH_JS, ""]

    # ファイル保存
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{timestamp}-neta-trend.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"保存: {out_path}")
    return str(out_path)


if __name__ == "__main__":
    main()
