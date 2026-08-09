#!/usr/bin/env python3
"""從 MCQ 系統把「有脈絡的綜論」導出成 raw/<年>-<題號>.txt。

只在本機跑 —— CI 不需要它（raw/ 已經 commit 進 repo），也拿不到 MCQ 金鑰。

產出的 txt 長這樣:

    第一一四年 第三十一集
    有脈絡的綜論:不去搶 ATP 的位子:一個替激酶裝回煞車的藥

    以下綜論將 ...

第一行是集號（synth 的開場白），第二行是標題，空行後是正文。
參考文獻與 OpenEvidence 尾註在這一步就砍掉 —— raw/ 應該是「可以直接唸」的乾淨稿，
而不是還要 CI 再去猜哪裡該切。

用法:
    python3 scripts/export_notes.py 114              # 整年 001-100
    python3 scripts/export_notes.py 114 --only 31,53 # 只導這幾題
    python3 scripts/export_notes.py 114 --dry-run    # 只印會寫什麼，不落地
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "raw"
ENV_FILE = ROOT / ".env"

# 綜論那一則的辨識依據。標點在 114 年中途從半形換成全形（001-066 用 ':'，
# 067 之後用 '：'），所以只比對前四個字，不比對標點。
NOTE_MARKER = "有脈絡的綜論"

# 參考文獻標題 —— 114 年 100 題全部是這一個寫法，但別的年份未必，所以放寬。
REF_HEADING = re.compile(r"^#{1,3}\s*(參考文獻|參考資料|References?)\b", re.I | re.M)
OE_FOOTER = re.compile(r"^>?\s*OpenEvidence 原始對話.*$", re.M)

_DIGITS = "〇一二三四五六七八九"


def year_to_speech(year: int) -> str:
    """114 → 「一一四」。民國年逐字唸，不是「一百一十四」。"""
    return "".join(_DIGITS[int(d)] for d in str(year))


def episode_to_speech(n: int) -> str:
    """53 → 「五十三」。集數用一般中文讀法，不逐字。"""
    if not 1 <= n <= 999:
        raise ValueError(f"集號超出支援範圍: {n}")
    if n < 10:
        return _DIGITS[n]
    if n < 20:
        return "十" + (_DIGITS[n % 10] if n % 10 else "")
    if n < 100:
        return _DIGITS[n // 10] + "十" + (_DIGITS[n % 10] if n % 10 else "")
    head = _DIGITS[n // 100] + "百"
    rest = n % 100
    if rest == 0:
        return head
    if rest < 10:  # 105 → 一百零五
        return head + "零" + _DIGITS[rest]
    return head + episode_to_speech(rest)


def load_env() -> dict:
    if not ENV_FILE.exists():
        sys.exit(
            f"找不到 {ENV_FILE}\n"
            "從 mcq.skill 解出來的 .env 複製過來即可（它含個人金鑰，已被 .gitignore 擋住）。"
        )
    cfg = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    missing = [k for k in ("MCQ_API_BASE", "MCQ_API_KEY", "MCQ_USER_EMAIL") if not cfg.get(k)]
    if missing:
        sys.exit(f"{ENV_FILE} 缺少: {', '.join(missing)}")
    return cfg


def fetch(cfg: dict, qid: str) -> dict:
    headers = {
        "Authorization": f"Bearer {cfg['MCQ_API_KEY']}",
        "X-User-Email": cfg["MCQ_USER_EMAIL"],
        # Cloudflare 會擋掉預設的 Python-urllib UA（error 1010）。
        "User-Agent": "edge-tts-export/0.1 (+claude-code)",
    }
    req = urllib.request.Request(f"{cfg['MCQ_API_BASE']}/api/mcq/{qid}", headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def pick_note(data: dict) -> dict | None:
    """找出綜論那一則。slot 編號在 114 年是 #3~#6 浮動，所以只能靠標題比對。"""
    notes = data.get("personal_notes")
    if notes is None:
        one = data.get("personal_note")
        notes = [one] if one else []
    hits = [n for n in notes if n and NOTE_MARKER in (n.get("title") or "")]
    if not hits:
        return None
    if len(hits) > 1:
        # 真的撞到就取 slot 最大的那則（比較新），但要讓人看見。
        print(f"  ⚠️  {data['id']} 有 {len(hits)} 則標題含「{NOTE_MARKER}」，取 slot 最大者", file=sys.stderr)
        hits.sort(key=lambda n: n.get("slot", 0))
    return hits[-1]


def strip_references(md: str) -> tuple[str, list[str]]:
    """砍掉參考文獻區段與 OpenEvidence 尾註。回傳 (正文, 砍了什麼的說明)。"""
    removed = []
    m = REF_HEADING.search(md)
    if m:
        removed.append(f"參考文獻起於第 {md[: m.start()].count(chr(10)) + 1} 行，砍掉 {len(md) - m.start()} 字")
        md = md[: m.start()]
    else:
        removed.append("⚠️ 找不到參考文獻標題 —— 請確認這則綜論的結構")
    md, n = OE_FOOTER.subn("", md)
    if n:
        removed.append(f"OpenEvidence 尾註 ×{n}")
    return md.rstrip() + "\n", removed


def dedupe_title(md: str, title: str) -> str:
    """API 的 markdown 第一行常常就是標題本身（網頁的下拉也是這樣取名的），
    而我們會另外把標題寫成 txt 第二行，所以這裡要把重複的那行拿掉。"""
    lines = md.lstrip().splitlines()
    if lines and lines[0].strip().rstrip("：:").strip() == title.strip().rstrip("：:").strip():
        return "\n".join(lines[1:]).lstrip()
    return md.lstrip()


def build_txt(year: int, number: int, note: dict) -> tuple[str, list[str]]:
    title = (note.get("title") or "").strip()
    body, notes = strip_references(note["markdown"])
    body = dedupe_title(body, title)
    header = f"第{year_to_speech(year)}年 第{episode_to_speech(number)}集"
    return f"{header}\n{title}\n\n{body}", notes


def export_one(cfg: dict, year: int, number: int, dry: bool) -> tuple[str, bool, str]:
    qid = f"{year}-{number:03d}"
    try:
        data = fetch(cfg, qid)
    except urllib.error.HTTPError as e:
        return qid, False, f"API {e.code}"
    except Exception as e:  # noqa: BLE001 — 網路層什麼都可能丟
        return qid, False, f"{type(e).__name__}: {e}"

    note = pick_note(data)
    if note is None:
        return qid, False, f"沒有標題含「{NOTE_MARKER}」的筆記"

    txt, warnings = build_txt(year, number, note)
    dest = RAW / f"{qid}.txt"
    if not dry:
        dest.write_text(txt, encoding="utf-8")
    chars = len(re.sub(r"\s", "", txt))
    return qid, True, f"{chars} 字 · slot #{note.get('slot')} · {'; '.join(warnings)}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("year", type=int, help="民國年，例如 114")
    ap.add_argument("--only", help="只導這幾題，逗號分隔，例如 31,53")
    ap.add_argument("--first", type=int, default=1)
    ap.add_argument("--last", type=int, default=100)
    ap.add_argument("--jobs", type=int, default=6, help="並行數（對 API 客氣一點）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_env()
    RAW.mkdir(exist_ok=True)
    numbers = (
        [int(x) for x in args.only.split(",")]
        if args.only
        else list(range(args.first, args.last + 1))
    )

    ok = fail = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(export_one, cfg, args.year, n, args.dry_run): n for n in numbers}
        for fut in concurrent.futures.as_completed(futures):
            qid, good, msg = fut.result()
            print(f"{'✅' if good else '❌'} {qid}  {msg}")
            ok, fail = (ok + 1, fail) if good else (ok, fail + 1)

    print(f"\n完成: {ok} 成功 / {fail} 失敗" + ("（dry-run，未寫檔）" if args.dry_run else ""))
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
