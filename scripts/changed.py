#!/usr/bin/env python3
"""決定這次 CI 要合成哪幾集，並把清單切成 matrix 用的 chunk。

兩層過濾，缺一不可:

  1. git diff —— 這次 push 動到了哪些 raw/*.txt
     解決「不要每次 push 都重跑 100 集」。

  2. 正規化後的 sha256 —— 內容真的變了嗎
     解決「改一個錯字就重跑 40 分鐘」。raw 改了但朗讀稿一模一樣（例如只調整了
     markdown 縮排），就不該重新合成。比對的是 build/manifest.json 裡的 sha256。

git 的邊界情況都有處理:
  - 首次 push / force push → github.event.before 是全 0 → 退回全量
  - 淺 clone 抓不到 before → 退回全量
  - 檔案被刪除 → 另外列進 deleted，讓 CI 去砍對應的 release
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from normalize import load_lexicon, normalize_one  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw"
MANIFEST = ROOT / "build" / "manifest.json"
ZERO_SHA = "0" * 40


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def diff_files(before: str, after: str) -> tuple[list[Path], list[str]]:
    """回傳 (新增或修改的 raw txt, 被刪除的 qid)。"""
    if not before or before == ZERO_SHA:
        return sorted(RAW.glob("*.txt")), []
    try:
        git("cat-file", "-e", f"{before}^{{commit}}")
    except subprocess.CalledProcessError:
        print(f"⚠️  取不到 before={before}（淺 clone 或 force push）→ 退回全量", file=sys.stderr)
        return sorted(RAW.glob("*.txt")), []

    out = git("diff", "--name-status", f"{before}..{after}", "--", "raw/")
    changed, deleted = [], []
    for line in out.splitlines():
        if not line.strip():
            continue
        status, _, path = line.partition("\t")
        path = path.strip().split("\t")[-1]  # rename 會有兩個路徑，取新的
        if not path.endswith(".txt"):
            continue
        if status.startswith("D"):
            deleted.append(Path(path).stem)
        else:
            changed.append(ROOT / path)
    return changed, deleted


def load_manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {}


def chunkify(items: list, n: int) -> list[list]:
    """把清單平均切成最多 n 份（GitHub matrix 上限 256，我們遠低於此）。"""
    if not items:
        return []
    n = max(1, min(n, len(items)))
    size, extra = divmod(len(items), n)
    out, i = [], 0
    for k in range(n):
        take = size + (1 if k < extra else 0)
        out.append(items[i : i + take])
        i += take
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--before", default="", help="github.event.before")
    ap.add_argument("--after", default="HEAD")
    ap.add_argument("--all", action="store_true", help="忽略 git diff，檢查全部 raw/")
    ap.add_argument("--only", help="只處理這幾題，逗號分隔（workflow_dispatch 用）")
    ap.add_argument("--force", action="store_true", help="忽略 sha256 比對，強制重新合成")
    ap.add_argument("--chunks", type=int, default=10, help="切成幾個 matrix job")
    args = ap.parse_args()

    if args.only:
        files = [RAW / f"{q.strip()}.txt" for q in args.only.split(",")]
        missing = [f for f in files if not f.exists()]
        if missing:
            sys.exit(f"找不到: {', '.join(str(m) for m in missing)}")
        deleted = []
    elif args.all:
        files, deleted = sorted(RAW.glob("*.txt")), []
    else:
        files, deleted = diff_files(args.before, args.after)

    manifest = load_manifest()
    rules = load_lexicon()
    todo, skipped = [], []
    for f in files:
        if not f.exists():
            continue
        meta = normalize_one(f, rules)
        prev = manifest.get(meta["qid"], {})
        if not args.force and prev.get("sha256") == meta["sha256"] and prev.get("mp3_released"):
            skipped.append(meta["qid"])
            continue
        todo.append(meta)

    todo.sort(key=lambda m: m["qid"])
    chunks = chunkify([m["qid"] for m in todo], args.chunks)

    result = {
        "todo": [m["qid"] for m in todo],
        "meta": {m["qid"]: m for m in todo},
        "skipped": skipped,
        "deleted": deleted,
        "chunks": chunks,
        "has_work": bool(todo or deleted),
    }
    print(json.dumps(result, ensure_ascii=False))

    print(
        f"\n📋 要合成 {len(todo)} 集 · 內容未變略過 {len(skipped)} 集 · 待刪 {len(deleted)} 集"
        f" · 切成 {len(chunks)} 個 job",
        file=sys.stderr,
    )
    if skipped:
        print(f"   略過: {', '.join(skipped)}", file=sys.stderr)


if __name__ == "__main__":
    main()
