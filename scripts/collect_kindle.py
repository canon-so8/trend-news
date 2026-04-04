#!/usr/bin/env python3
"""
Kindle 日替わりセール本 収集スクリプト
sale-bon.com/daily_sale/ から個別書籍を取得し、
yapi.ta2o.net/kndlsl/ からセールキャンペーン一覧を補足してJekyll Markdownを生成
"""
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

JST = timezone(timedelta(hours=9))
REPO_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = REPO_ROOT / "_posts" / "kindle"

SALE_BON_URL = "https://sale-bon.com/daily_sale/"

CSS = """<style>
.kindle-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 16px; margin: 1.2rem 0; }
.kindle-card { border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden; transition: box-shadow .2s; }
.kindle-card:hover { box-shadow: 0 4px 12px rgba(0,0,0,.12); }
.kindle-card a { text-decoration: none; color: inherit; display: block; }
.kindle-cover { width: 100%; aspect-ratio: 2/3; object-fit: contain; background: #f5f5f5; display: block; }
.kindle-cover-placeholder { width: 100%; aspect-ratio: 2/3; background: #f0f0f0; display: flex; align-items: center; justify-content: center; color: #bbb; font-size: 0.7rem; }
.kindle-info { padding: 8px; }
.kindle-title { font-size: 0.78rem; font-weight: 700; line-height: 1.3; margin-bottom: 4px; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.kindle-author { font-size: 0.7rem; color: #888; margin-bottom: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.kindle-price { font-size: 0.82rem; font-weight: 700; color: #c0392b; }
.section-title { font-size: 1rem; font-weight: 700; margin: 1.6rem 0 0.6rem; border-left: 4px solid #ff9900; padding-left: 8px; }
.sale-campaign { margin-bottom: 0.8rem; padding: 10px 14px; background: #fffbf0; border: 1px solid #ffe0a0; border-radius: 6px; }
.sale-campaign a { color: #c07000; font-weight: 700; }
.sale-campaign .sale-meta { font-size: 0.75rem; color: #888; margin-top: 3px; }
.tag { font-size: 0.7rem; font-weight: 700; padding: 1px 6px; border-radius: 3px; }
</style>"""


def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1.5, status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ja,en;q=0.9",
    })
    return s


SESSION = _session()


def fetch_html(url: str) -> str | None:
    try:
        resp = SESSION.get(url, timeout=30)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text
    except Exception as e:
        print(f"  取得失敗 {url}: {e}", file=sys.stderr)
        return None


# ---------- sale-bon.com: 個別書籍 ----------

def fetch_sale_bon_books() -> list[dict]:
    """sale-bon.com/daily_sale/ から日替わりセール書籍一覧を取得"""
    html = fetch_html(SALE_BON_URL)
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    books = []

    for row in soup.select("div.series-row"):
        try:
            # タイトル
            title_el = row.select_one("h2.series_title a")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            if not title:
                continue

            # Amazon リンク
            link_el = row.select_one("a[rel='sponsored']")
            amazon_url = link_el["href"] if link_el else ""

            # セール価格
            price_el = row.select_one("h3.description")
            price = price_el.get_text(strip=True) if price_el else ""

            # 著者・出版社（`著者：名前` 形式）
            author = ""
            publisher = ""
            confirmed = ""
            for li in row.select("ul.other li"):
                text = li.get_text(strip=True)
                if text.startswith("著者："):
                    author = text.removeprefix("著者：")
                elif text.startswith("出版社："):
                    publisher = text.removeprefix("出版社：")
                elif text.startswith("確認日時："):
                    confirmed = text.removeprefix("確認日時：")

            # カバー画像（小サイズ → 中サイズに差し替え）
            img_el = row.select_one("div.comic-photo img")
            img_url = ""
            if img_el:
                img_url = img_el.get("src", "")
                if img_url.startswith("//"):
                    img_url = "https:" + img_url
                # _SL160_ → _SL500_ で高解像度化
                img_url = re.sub(r'_SL\d+_', '_SL500_', img_url)

            books.append({
                "title": title,
                "author": author,
                "publisher": publisher,
                "price": price,
                "amazon_url": amazon_url,
                "img_url": img_url,
                "confirmed": confirmed,
            })
        except Exception as e:
            print(f"  書籍パース失敗: {e}", file=sys.stderr)

    print(f"  sale-bon.com: {len(books)} 件")
    return books


# ---------- Markdown 生成 ----------

def build_markdown(books: list[dict], now: datetime) -> str:
    date_label = now.strftime("%Y-%m-%d")
    time_label = now.strftime("%H:%M")

    lines: list[str] = [
        "---",
        "layout: post",
        f'title: "{date_label} : Kindle 日替わりセール"',
        f"date: {date_label} {time_label}:00 +0900",
        "categories: [kindle]",
        "---",
        "",
        CSS,
        "",
    ]

    # --- 日替わりセール書籍グリッド ---
    if books:
        lines += [
            f'<p class="section-title">日替わりセール書籍（{len(books)}冊）</p>',
            "",
            '<div class="kindle-grid">',
        ]
        for book in books:
            amazon_href = book["amazon_url"] or "#"
            img_html = (
                f'<img class="kindle-cover" src="{book["img_url"]}" alt="{book["title"]}" loading="lazy">'
                if book["img_url"]
                else '<div class="kindle-cover-placeholder">No Image</div>'
            )
            author_html = (
                f'<div class="kindle-author">{book["author"]}</div>'
                if book["author"] else ""
            )
            price_html = (
                f'<div class="kindle-price">{book["price"]}</div>'
                if book["price"] else ""
            )
            lines += [
                f'<div class="kindle-card">',
                f'  <a href="{amazon_href}" target="_blank" rel="noopener">',
                f'    {img_html}',
                f'    <div class="kindle-info">',
                f'      <div class="kindle-title">{book["title"]}</div>',
                author_html,
                price_html,
                f'    </div>',
                f'  </a>',
                f'</div>',
            ]
        lines += ["</div>", ""]
    else:
        lines += [
            '<p>本日の日替わりセール書籍を取得できませんでした。</p>',
            f'<p><a href="{SALE_BON_URL}" target="_blank">sale-bon.com で確認する</a></p>',
            "",
        ]

    return "\n".join(lines)


# ---------- main ----------

def main():
    now = datetime.now(JST)

    print("sale-bon.com から日替わりセール書籍を取得...")
    books = fetch_sale_bon_books()

    if not books:
        print("データ取得失敗。終了します。", file=sys.stderr)
        sys.exit(1)

    # Markdown 生成・保存
    content = build_markdown(books, now)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = now.strftime("%Y-%m-%d-%H-%M")
    out_path = OUTPUT_DIR / f"{timestamp}-kindle-sale.md"
    out_path.write_text(content, encoding="utf-8")
    print(f"保存: {out_path}")

    # Slack 通知用 URL
    date_path = now.strftime("%Y/%m/%d")
    time_slug = now.strftime("%H-%M")
    page_url = f"https://canon-so8.github.io/news-claw/kindle/{date_path}/{time_slug}-kindle-sale/"
    try:
        Path("/tmp/kindle_url.txt").write_text(page_url)
    except Exception:
        pass

    return str(out_path)


if __name__ == "__main__":
    main()
