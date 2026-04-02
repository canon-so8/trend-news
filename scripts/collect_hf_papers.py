#!/usr/bin/env python3
"""
HF Daily Papers 収集スクリプト
Claude Code 不要 - HF API から直接データ取得してJekyll Markdownを生成
"""
import json
import os
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def fetch_papers(date_str: str) -> list[dict]:
    url = f"https://huggingface.co/api/daily_papers?date={date_str}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"  {date_str}: fetch error ({e})", file=sys.stderr)
        return []


def main():
    now = datetime.now(JST)
    dates = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]

    # 7日分を並列取得
    results: dict[str, list] = {}
    with ThreadPoolExecutor(max_workers=7) as ex:
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
                "published_at": (paper.get("publishedAt") or "")[:10],
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

    # タグ付け
    for p in filtered:
        p["tags"] = assign_tags(p)

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
        github_link = f' · [GitHub]({p["github_repo"]})' if p["github_repo"] else ""
        arxiv_url = f'https://arxiv.org/abs/{p["id"]}'

        summary = p["summary"].replace("\n", " ").strip()

        lines += [
            f'<div class="paper" data-tags="{data_tags}" markdown="1">',
            "",
            f'**[{p["title"]}]({arxiv_url})**',
            "",
            f'{tag_spans} {upvote_span}{star_span} · {p["published_at"]} · {p["first_author"]}{github_link}',
            "",
            f'{summary}',
            "",
            "</div>",
            "",
        ]

    lines += [FILTER_JS, ""]

    # ファイル保存
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{timestamp}-neta-trend-hf.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"保存: {out_path}")
    return str(out_path)


if __name__ == "__main__":
    main()
