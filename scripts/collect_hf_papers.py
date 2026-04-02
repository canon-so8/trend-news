#!/usr/bin/env python3
"""
HF Daily Papers 収集スクリプト
Claude Code 不要 - HF API から直接データ取得してJekyll Markdownを生成
"""
import json
import os
import re
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

JST = timezone(timedelta(hours=9))
REPO_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = REPO_ROOT / "_posts" / "arxiv"

TAGS_RULES = {
    "agent": ["agent", "agentic", "multi-agent", "tool use", "react", "workflow", "planning"],
    "llm": ["language model", "llm", "large language", "gpt", "rlhf", "rag",
            "chain-of-thought", "reasoning", "instruction", "alignment", "fine-tun"],
    "ml": ["reinforcement learning", "optimization", "training", "generalization",
           "neural network", "gradient", "attention mechanism"],
    "cv": ["image", "video", "vision", "diffusion", "generation", "segmentation",
           "detection", "3d", "rendering", "gan", "vae"],
    "nlp": ["nlp", "text", "translation", "summarization", "sentiment", "parsing",
            "embedding", "tokeniz"],
    "audio": ["audio", "speech", "music", "sound", "voice", "tts", "asr", "acoustic"],
    "quant": ["quantum", "qubit", "quantum circuit"],
}

CSS = """<style>
.tag { font-size: 0.72rem; font-weight: 700; padding: 2px 7px; border-radius: 3px; white-space: nowrap; }
.tag-cv    { color: #1a6bbf; background: #e8f0fb; }
.tag-ml    { color: #6a1b9a; background: #f3e5f5; }
.tag-nlp   { color: #2a8a4a; background: #e6f4ea; }
.tag-agent { color: #bf5a00; background: #fff3e0; }
.tag-audio { color: #c2185b; background: #fce4ec; }
.tag-llm   { color: #00695c; background: #e0f2f1; }
.tag-quant { color: #1a4f7a; background: #e3f0fb; }
.tag-hf    { color: #ff6b35; background: #fff0eb; }
.tag-other { color: #666; background: #f2f2f2; }
.tab-nav { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 1.2rem; }
.tab-btn { padding: 5px 13px; border: none; border-radius: 20px; cursor: pointer; font-size: 0.82rem; font-weight: 700; background: #e8e8e8; color: #444; }
.tab-btn.active { background: #333; color: #fff; }
.paper { margin-bottom: 0; border-top: 1px solid rgba(128,128,128,0.35); padding-top: 1.2rem; margin-top: 0.8rem; }
.paper:first-of-type { border-top: none; padding-top: 0; margin-top: 0; }
details summary { cursor: pointer; font-size: 0.78rem; color: #888; margin-top: 4px; }
details p { font-size: 0.82rem; margin: 4px 0 0 12px; color: #555; }
</style>"""

TAB_NAV = """<div class="tab-nav">
  <button class="tab-btn active" onclick="filterTag('all',this)">ALL</button>
  <button class="tab-btn" onclick="filterTag('agent',this)">Agent</button>
  <button class="tab-btn" onclick="filterTag('ml',this)">ML</button>
  <button class="tab-btn" onclick="filterTag('cv',this)">CV</button>
  <button class="tab-btn" onclick="filterTag('nlp',this)">NLP</button>
  <button class="tab-btn" onclick="filterTag('llm',this)">LLM</button>
  <button class="tab-btn" onclick="filterTag('audio',this)">Audio</button>
  <button class="tab-btn" onclick="filterTag('quant',this)">Quant</button>
</div>"""

FILTER_JS = """<script>
function filterTag(tag, btn) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.paper').forEach(p => {
    p.style.display = (tag === 'all' || p.dataset.tags.split(' ').includes(tag)) ? '' : 'none';
  });
}
</script>"""


def assign_tags(paper: dict) -> list[str]:
    text = (
        paper.get("title", "") + " " +
        paper.get("summary", "") + " " +
        " ".join(paper.get("ai_keywords", []))
    ).lower()
    tags = [tag for tag, keywords in TAGS_RULES.items() if any(kw in text for kw in keywords)]
    return tags or ["other"]


DEEPL_AUTH_KEY = os.environ.get("DEEPL_AUTH_KEY", "")


DEEPL_API_URL = (
    "https://api-free.deepl.com/v2/translate"
    if DEEPL_AUTH_KEY.endswith(":fx")
    else "https://api.deepl.com/v2/translate"
)


def translate_deepl(text: str) -> str:
    """DeepL API で英語→日本語翻訳（Free/Pro自動判定）"""
    if not DEEPL_AUTH_KEY or not text:
        return ""
    try:
        resp = requests.post(
            DEEPL_API_URL,
            headers={"Authorization": f"DeepL-Auth-Key {DEEPL_AUTH_KEY}"},
            data={"text": text[:1500], "target_lang": "JA"},
            timeout=15,
        )
        if resp.status_code == 200:
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
        f"?client=gtx&sl=en&tl=ja&dt=t&q={urllib.parse.quote(text[:500])}"
    )
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
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


def _make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1.5, status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Accept": "application/json",
    })
    return session


def fetch_papers(date_str: str) -> list[dict]:
    url = f"https://huggingface.co/api/daily_papers?date={date_str}"
    for attempt in range(3):
        try:
            session = _make_session()
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            print(f"  {date_str}: {len(data)} 件")
            return data
        except Exception as e:
            print(f"  {date_str}: attempt {attempt+1} error ({e})", file=sys.stderr)
            if attempt < 2:
                time.sleep(2 ** attempt)
    return []


def main():
    now = datetime.now(JST)
    dates = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]

    # 7日分を並列取得（max_workers=3でレート制限回避）
    results: dict[str, list] = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(fetch_papers, d): d for d in dates}
        for fut in as_completed(futures):
            d = futures[fut]
            results[d] = fut.result()

    # 重複排除しながら統合
    seen_ids: set[str] = set()
    all_papers: list[dict] = []
    for d in dates:
        for entry in results.get(d, []):
            paper = entry.get("paper", {})
            pid = paper.get("id")
            if not pid or pid in seen_ids:
                continue
            seen_ids.add(pid)
            all_papers.append({
                "id": pid,
                "title": paper.get("title", ""),
                "summary": paper.get("summary", ""),
                "upvotes": paper.get("upvotes", 0) or 0,
                "github_stars": paper.get("githubStars", 0) or 0,
                "github_repo": paper.get("githubRepo"),
                "hf_date": d,
                "first_author": ((paper.get("authors") or [{}])[0]).get("name", ""),
                "ai_keywords": paper.get("ai_keywords", []),
            })

    print(f"取得: {len(all_papers)} 件（重複排除後）")

    # フィルタリング
    filtered = [p for p in all_papers if p["upvotes"] >= 5 or p["github_stars"] >= 10]
    if len(filtered) < 5:
        filtered = [p for p in all_papers if p["upvotes"] >= 2]
    filtered.sort(key=lambda p: p["upvotes"], reverse=True)
    filtered = filtered[:30]

    print(f"フィルタ後: {len(filtered)} 件")

    # タグ付け + Google Translateでアブスト翻訳
    for p in filtered:
        p["tags"] = assign_tags(p)

    # DeepL/Google Translateでアブスト翻訳
    if DEEPL_AUTH_KEY:
        endpoint = "api-free" if DEEPL_AUTH_KEY.endswith(":fx") else "api (Pro)"
        print(f"  アブスト翻訳中... ({len(filtered)}件, DeepL={endpoint}, key=...{DEEPL_AUTH_KEY[-6:]})")
    else:
        print(f"  アブスト翻訳中... ({len(filtered)}件, Google Translate)")
    for p in filtered:
        p["summary_ja"] = translate_ja(p["summary"])
        time.sleep(0.3)

    # Markdown生成
    timestamp = now.strftime("%Y-%m-%d-%H-%M")
    date_label = now.strftime("%Y-%m-%d")
    time_label = now.strftime("%H:%M")

    lines: list[str] = [
        "---",
        "layout: post",
        f'title: "{date_label}:トレンド論文"',
        f"date: {date_label} {time_label}:00 +0900",
        "categories: [arxiv]",
        "---",
        "",
        CSS,
        "",
        TAB_NAV,
        "",
    ]

    for p in filtered:
        tags = p["tags"]
        data_tags = " ".join(tags)
        tag_spans = " ".join(
            f'<span class="tag tag-{t}">{t.upper()}</span>' for t in tags
        )
        upvote_span = f'<span class="tag tag-hf">▲ {p["upvotes"]} upvotes</span>'
        star_span = (
            f' <span class="tag tag-hf">★ {p["github_stars"]} stars</span>'
            if p["github_stars"] > 0 else ""
        )
        github_link = f' · <a href="{p["github_repo"]}">GitHub</a>' if p["github_repo"] else ""
        arxiv_url = f'https://arxiv.org/abs/{p["id"]}'
        hf_date = p.get("hf_date", "")

        summary_ja = p.get("summary_ja", "")

        details_block = ""
        if summary_ja:
            details_block = (
                '<details>'
                '<summary>要約を読む</summary>'
                f'<p>{summary_ja}</p>'
                '</details>'
            )

        lines += [
            f'<div class="paper" data-tags="{data_tags}">',
            f'<p><strong><a href="{arxiv_url}">{p["title"]}</a></strong></p>',
            f'<p>{tag_spans} {upvote_span}{star_span} · {hf_date[5:]} · {p["first_author"]}{github_link}</p>',
            details_block,
            "</div>",
            "",
        ]

    lines += [FILTER_JS, ""]

    # ファイル保存
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{timestamp}-neta-trend-hf.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"保存: {out_path}")

    # Slack通知用URLをファイルに書き出す
    date_path = now.strftime("%Y/%m/%d")
    time_slug = now.strftime("%H-%M")
    page_url = f"https://canon-so8.github.io/trend-news/arxiv/{date_path}/{time_slug}-neta-trend-hf/"
    try:
        Path("/tmp/hf_url.txt").write_text(page_url)
    except Exception:
        pass
    return str(out_path)


if __name__ == "__main__":
    main()
