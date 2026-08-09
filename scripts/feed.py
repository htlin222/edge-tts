#!/usr/bin/env python3
"""build/manifest.json → site/feed.xml + site/index.html

因為每一集是獨立的 release（tag = 題號），mp3 的 URL 分散在各個 release 底下，
沒有一個穩定的地方可以放 feed。所以 feed 走 GitHub Pages：

    https://<owner>.github.io/<repo>/feed.xml   ← 永久不變，貼進 podcast app
      └─ enclosure → https://github.com/<owner>/<repo>/releases/download/114-031/114-031.mp3

RSS 的 <item> 順序決定 podcast app 的播放順序。這裡按題號**倒序**排（新的在前），
因為那是 RSS 的慣例；播放器都能改成正序。
"""

from __future__ import annotations

import argparse
import html
import json
import os
from email.utils import format_datetime
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "build" / "manifest.json"
SITE = ROOT / "site"

PODCAST_TITLE = "有脈絡的綜論"
PODCAST_SUBTITLE = "血液腫瘤次專科考古題 · 一題一集"
PODCAST_DESC = (
    "把每一題考古題的「有脈絡的綜論」唸出來。"
    "內容是個人筆記，不是教科書；聽的時候請自行核對原始文獻。"
    "由 edge-tts 以 zh-TW 神經語音合成。"
)
AUTHOR = "htlin222"
LANGUAGE = "zh-TW"


def esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def fmt_duration(sec: float) -> str:
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def build_feed(manifest: dict, owner: str, repo: str) -> str:
    site_url = f"https://{owner}.github.io/{repo}"
    items = []
    for qid, m in sorted(manifest.items(), reverse=True):
        if not m.get("mp3_released"):
            continue
        url = f"https://github.com/{owner}/{repo}/releases/download/{qid}/{qid}.mp3"
        pub = m.get("released_at") or datetime.now(timezone.utc).isoformat()
        try:
            dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
        except ValueError:
            dt = datetime.now(timezone.utc)
        title = m.get("title") or qid
        # 標題本身常帶「有脈絡的綜論:」前綴，節目名已經講過一次，去掉免得每集都重複
        short = title.split(":", 1)[-1].split("：", 1)[-1].strip()
        items.append(
            f"""    <item>
      <title>{esc(f'{qid} {short}')}</title>
      <description>{esc(m.get('episode', ''))} · {m.get('chars', 0)} 字</description>
      <link>https://github.com/{owner}/{repo}/releases/tag/{qid}</link>
      <guid isPermaLink="false">{esc(qid)}</guid>
      <pubDate>{format_datetime(dt)}</pubDate>
      <enclosure url="{esc(url)}" length="{m.get('bytes', 0)}" type="audio/mpeg"/>
      <itunes:duration>{fmt_duration(m.get('duration', 0))}</itunes:duration>
      <itunes:episode>{int(qid.split('-')[1])}</itunes:episode>
      <itunes:season>{int(qid.split('-')[0])}</itunes:season>
      <itunes:explicit>false</itunes:explicit>
    </item>"""
        )

    cover = f"{site_url}/cover.png" if (SITE / "cover.png").exists() else ""
    cover_tag = f'\n    <itunes:image href="{esc(cover)}"/>' if cover else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>{esc(PODCAST_TITLE)}</title>
    <link>{esc(site_url)}</link>
    <atom:link href="{esc(site_url)}/feed.xml" rel="self" type="application/rss+xml"/>
    <description>{esc(PODCAST_DESC)}</description>
    <language>{LANGUAGE}</language>
    <itunes:author>{esc(AUTHOR)}</itunes:author>
    <itunes:subtitle>{esc(PODCAST_SUBTITLE)}</itunes:subtitle>
    <itunes:summary>{esc(PODCAST_DESC)}</itunes:summary>
    <itunes:explicit>false</itunes:explicit>
    <itunes:category text="Education"/>
    <itunes:type>episodic</itunes:type>{cover_tag}
{chr(10).join(items)}
  </channel>
</rss>
"""


def build_index(manifest: dict, owner: str, repo: str) -> str:
    rows, total_sec, count = [], 0.0, 0
    for qid, m in sorted(manifest.items()):
        if not m.get("mp3_released"):
            continue
        count += 1
        total_sec += m.get("duration", 0)
        title = (m.get("title") or qid).split(":", 1)[-1].split("：", 1)[-1].strip()
        base = f"https://github.com/{owner}/{repo}/releases/download/{qid}"
        rows.append(
            f"""    <tr>
      <td><code>{esc(qid)}</code></td>
      <td>{esc(title)}</td>
      <td class="num">{fmt_duration(m.get('duration', 0))}</td>
      <td class="num">{m.get('chars', 0):,}</td>
      <td><a href="{base}/{qid}.mp3">mp3</a> · <a href="{base}/{qid}.vtt">字幕</a></td>
    </tr>"""
        )

    feed_url = f"https://{owner}.github.io/{repo}/feed.xml"
    return f"""<!doctype html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(PODCAST_TITLE)}</title>
<style>
  :root {{ color-scheme: light dark; --fg:#1a1a1a; --bg:#fdfdfc; --mute:#666; --line:#e3e3e0; --acc:#0b5; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --fg:#e8e8e6; --bg:#161615; --mute:#999; --line:#333; --acc:#3d8; }}
  }}
  body {{ margin:0 auto; padding:2rem 1.25rem 4rem; max-width:56rem; background:var(--bg); color:var(--fg);
         font:16px/1.65 -apple-system,"Noto Sans TC",sans-serif; }}
  h1 {{ font-size:1.6rem; margin:0 0 .25rem; }}
  .sub {{ color:var(--mute); margin:0 0 1.5rem; }}
  .feed {{ background:color-mix(in srgb, var(--acc) 12%, transparent); border:1px solid var(--acc);
           border-radius:8px; padding:.75rem 1rem; margin-bottom:2rem; }}
  .feed code {{ user-select:all; word-break:break-all; }}
  .wrap {{ overflow-x:auto; }}
  table {{ border-collapse:collapse; width:100%; font-size:.92rem; }}
  th,td {{ text-align:left; padding:.45rem .6rem; border-bottom:1px solid var(--line); }}
  th {{ color:var(--mute); font-weight:600; white-space:nowrap; }}
  .num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
  a {{ color:var(--acc); }}
  footer {{ margin-top:2.5rem; color:var(--mute); font-size:.85rem; }}
</style>
</head>
<body>
<h1>{esc(PODCAST_TITLE)}</h1>
<p class="sub">{esc(PODCAST_SUBTITLE)} · 共 {count} 集 · 總長 {fmt_duration(total_sec)}</p>
<div class="feed">
  <strong>訂閱網址</strong>（貼進 Pocket Casts / Overcast / Apple Podcasts）<br>
  <code>{esc(feed_url)}</code>
</div>
<div class="wrap">
<table>
  <thead><tr><th>題號</th><th>標題</th><th class="num">長度</th><th class="num">字數</th><th>下載</th></tr></thead>
  <tbody>
{chr(10).join(rows)}
  </tbody>
</table>
</div>
<footer>
  內容為個人筆記，非教科書，請自行核對原始文獻。語音由 edge-tts 合成。
  原始文字與建置腳本：<a href="https://github.com/{owner}/{repo}">{owner}/{repo}</a>
</footer>
</body>
</html>
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "htlin222/edge-tts"),
                    help="owner/repo")
    args = ap.parse_args()
    owner, _, repo = args.repo.partition("/")

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    SITE.mkdir(exist_ok=True)
    (SITE / "feed.xml").write_text(build_feed(manifest, owner, repo), encoding="utf-8")
    (SITE / "index.html").write_text(build_index(manifest, owner, repo), encoding="utf-8")
    (SITE / ".nojekyll").write_text("", encoding="utf-8")

    published = sum(1 for m in manifest.values() if m.get("mp3_released"))
    print(f"✅ site/feed.xml + site/index.html （{published} 集已發佈）")


if __name__ == "__main__":
    main()
