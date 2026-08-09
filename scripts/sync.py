#!/usr/bin/env python3
"""把 release 上的音檔同步到本機，必要時再 rsync 到遠端。

為什麼不是三行 shell：**「檔案已存在就跳過」是錯的判斷。**
語速從 +20% 改成 +0% 之後，每一集的檔名完全不變、內容卻整個換掉了。
只看檔名存在與否，本機會永遠停在舊版本，而且不會有任何跡象。

所以這裡比對的是**檔案大小**（release asset 的 size 對本機的 st_size）。
大小一樣就跳過，不一樣就重抓。這對「重新合成」這種整檔換掉的情況夠用，
也不必為了幾百 MB 的音檔去算 checksum。

用法:
    python3 scripts/sync.py                                  # → ~/Downloads/mcq-tts
    python3 scripts/sync.py --dest ~/Downloads
    python3 scripts/sync.py --rsync goanna:~/Downloads       # 同步完直接送過去
    python3 scripts/sync.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_REPO = "htlin222/edge-tts"
DEFAULT_DEST = Path.home() / "Downloads" / "mcq-tts"


def gh_json(*args: str):
    """呼叫 gh api。刻意不用 `gh release list` —— rtk 的 hook 會改寫它並吃掉 --json。"""
    out = subprocess.run(["gh", "api", *args], capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"gh api 失敗: {out.stderr.strip()}")
    return json.loads(out.stdout)


def list_assets(repo: str, pattern: str) -> list[dict]:
    releases = gh_json(f"repos/{repo}/releases", "--paginate")
    assets = []
    for rel in releases:
        for a in rel.get("assets", []):
            if a["name"].endswith(pattern):
                assets.append({"tag": rel["tag_name"], "name": a["name"], "size": a["size"]})
    assets.sort(key=lambda a: a["name"])
    return assets


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", DEFAULT_REPO))
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    ap.add_argument("--ext", default=".mp3", help="要同步的副檔名（預設只抓 mp3）")
    ap.add_argument("--rsync", metavar="REMOTE", help="同步完 rsync 過去，例如 goanna:~/Downloads")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    assets = list_assets(args.repo, args.ext)
    if not assets:
        sys.exit(f"{args.repo} 上找不到任何 {args.ext} —— CI 可能還在合成")

    args.dest.mkdir(parents=True, exist_ok=True)
    fresh = stale = new = 0
    for a in assets:
        local = args.dest / a["name"]
        if local.exists() and local.stat().st_size == a["size"]:
            fresh += 1
            continue
        why = "大小不符，重新下載" if local.exists() else "新增"
        if local.exists():
            stale += 1
        else:
            new += 1
        print(f"  ↓ {a['name']}  ({why})")
        if args.dry_run:
            continue
        r = subprocess.run(
            ["gh", "release", "download", a["tag"], "-R", args.repo,
             "-p", a["name"], "-D", str(args.dest), "--clobber"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"  ⚠️  {a['name']} 下載失敗: {r.stderr.strip()}", file=sys.stderr)

    total = len(list(args.dest.glob(f"*{args.ext}")))
    size_mb = sum(f.stat().st_size for f in args.dest.glob(f"*{args.ext}")) / 1e6
    print(
        f"\n✅ 新增 {new} · 更新 {stale} · 已是最新 {fresh}"
        f"　→ {args.dest} 共 {total} 檔 {size_mb:.0f} MB"
        + ("（dry-run）" if args.dry_run else "")
    )

    if args.rsync and not args.dry_run:
        print(f"\n→ rsync 到 {args.rsync}")
        subprocess.run(
            ["rsync", "-ah", "--partial", "--info=progress2",
             "--include", f"*{args.ext}", "--exclude", "*",
             f"{args.dest}/", f"{args.rsync}/"],
            check=True,
        )


if __name__ == "__main__":
    main()
