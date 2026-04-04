#!/usr/bin/env python3
"""
Kindle 日替わりセール リンク生成スクリプト
スクレイピングなし - Amazon公式ページへのリンクをJekyll Markdownに埋め込む
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
REPO_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = REPO_ROOT / "_posts" / "kindle"

KINDLE_DAILY_URL = "https://www.amazon.co.jp/kindle-dbs/browse/?widgetId=ebooks-deals-storefront_KindleDailyDealsStrategy"

CSS = """<style>
.kindle-link-box {
  margin: 2rem 0;
  padding: 1.2rem 1.6rem;
  border: 2px solid #ff9900;
  border-radius: 10px;
  background: #fffbf0;
  text-align: center;
}
.kindle-link-box a {
  font-size: 1.1rem;
  font-weight: 700;
  color: #c07000;
  text-decoration: none;
}
.kindle-link-box a:hover { text-decoration: underline; }
.kindle-note { font-size: 0.82rem; color: #888; margin-top: 0.6rem; }
</style>"""


def main():
    now = datetime.now(JST)
    date_label = now.strftime("%Y-%m-%d")
    time_label = now.strftime("%H:%M")
    timestamp  = now.strftime("%Y-%m-%d-%H-%M")

    lines = [
        "---",
        "layout: post",
        f'title: "{date_label} : Kindle 日替わりセール"',
        f"date: {date_label} {time_label}:00 +0900",
        "categories: [kindle]",
        "---",
        "",
        CSS,
        "",
        '<div class="kindle-link-box">',
        f'  <a href="{KINDLE_DAILY_URL}" target="_blank" rel="noopener">',
        f"    📚 {date_label} の Kindle 日替わりセールを見る",
        "  </a>",
        '  <div class="kindle-note">Amazon公式ページに移動します</div>',
        "</div>",
        "",
    ]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{timestamp}-kindle-sale.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"保存: {out_path}")

    date_path = now.strftime("%Y/%m/%d")
    time_slug  = now.strftime("%H-%M")
    page_url   = f"https://canon-so8.github.io/news-claw/kindle/{date_path}/{time_slug}-kindle-sale/"
    try:
        Path("/tmp/kindle_url.txt").write_text(page_url)
    except Exception:
        pass

    return str(out_path)


if __name__ == "__main__":
    main()
