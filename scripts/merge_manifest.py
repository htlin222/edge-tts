#!/usr/bin/env python3
"""把 detect 的計畫與各 matrix job 的合成結果合併成 build/manifest.json。

manifest 是這個 repo 的狀態記錄，回答三個問題:
  1. 這一集的朗讀稿 sha256 是多少 → 下次 push 要不要重新合成
  2. 這一集發佈了沒 → feed 要不要收它
  3. 這一集多長、多大 → RSS 的 <enclosure length> 與 <itunes:duration> 要用

只在 finalize job 寫一次。10 個 matrix job 各自 commit manifest 必然互相覆蓋，
所以它們只上傳 artifact，由這支腳本統一收斂。
"""

from __future__ import annotations

import argparse
import glob
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "build" / "manifest.json"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--synth", required=True, help="glob，例如 'artifacts/synth-*/synth.json'")
    ap.add_argument("--deleted", default="[]", help="JSON 陣列")
    args = ap.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}

    plan_path = Path(args.plan)
    plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else {"meta": {}}

    # 先寫入正規化的結果（sha256 / 字數 / 標題）
    for qid, meta in plan.get("meta", {}).items():
        entry = manifest.setdefault(qid, {})
        entry.update(
            {
                "qid": qid,
                "title": meta.get("title"),
                "episode": meta.get("episode"),
                "sha256": meta.get("sha256"),
                "chars": meta.get("chars"),
                "tables": meta.get("tables", []),
            }
        )
        # 這一輪要重做，先當作未發佈；下面合成成功才會標回 True
        entry["mp3_released"] = False

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    published = 0
    for path in sorted(glob.glob(args.synth)):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for row in data.get("ok", []):
            qid = row["qid"]
            entry = manifest.setdefault(qid, {"qid": qid})
            entry.update(
                {
                    "duration": row["duration"],
                    "bytes": row["bytes"],
                    "voice": row["voice"],
                    "rate": row["rate"],
                    "chunks": row["chunks"],
                    "mp3_released": True,
                    "released_at": now,
                }
            )
            published += 1
        for row in data.get("failed", []):
            qid = row["qid"]
            manifest.setdefault(qid, {"qid": qid})["last_error"] = row.get("error")

    for qid in json.loads(args.deleted or "[]"):
        manifest.pop(qid, None)

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(
        json.dumps(dict(sorted(manifest.items())), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    total = sum(1 for m in manifest.values() if m.get("mp3_released"))
    print(f"✅ manifest 更新：本輪發佈 {published} 集，累計已發佈 {total} 集")


if __name__ == "__main__":
    main()
