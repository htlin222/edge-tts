#!/usr/bin/env python3
"""raw/<qid>.txt → build/<qid>.speech.txt（朗讀稿）。

朗讀稿是「唸出來會是什麼」的唯一真相。它會被 commit 回 repo，所以：
  - 你可以直接讀它，發現哪個詞唸法不對 → 去改 dict/lexicon.yaml
  - CI 用它的 sha256 判斷要不要重新合成（改一個標點就重跑 40 分鐘太蠢）

edge-tts 沒有 SSML，所以「停頓」只能靠段落切分（由 synth.py 在段落之間插靜音）。
這裡的職責就是：把 markdown 攤平成一段一段乾淨的中文，其餘交給 synth。

用法:
    python3 scripts/normalize.py raw/114-031.txt
    python3 scripts/normalize.py raw/*.txt --diff   # 顯示每條 lexicon 規則改了什麼
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tables import expand_fences, expand_tables  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
LEXICON = ROOT / "dict" / "lexicon.yaml"


def load_lexicon() -> list[dict]:
    """讀 lexicon.yaml。刻意手寫解析而不依賴 PyYAML —— 這個檔的結構固定，
    少一個依賴就少一個 CI 會壞掉的理由。"""
    rules, cur, in_rules = [], None, False
    for raw_line in LEXICON.read_text(encoding="utf-8").splitlines():
        if raw_line.startswith("rules:"):
            in_rules = True
            continue
        if not in_rules:
            continue
        if raw_line and not raw_line[0].isspace() and not raw_line.startswith("#"):
            break  # 離開 rules: 區塊
        stripped = raw_line.strip()
        if stripped.startswith("- pattern:"):
            if cur:
                rules.append(cur)
            cur = {"pattern": _yaml_scalar(stripped.split(":", 1)[1])}
        elif stripped.startswith("replace:") and cur is not None:
            cur["replace"] = _yaml_scalar(stripped.split(":", 1)[1])
        elif stripped.startswith("note:") and cur is not None:
            cur.setdefault("note", stripped.split(":", 1)[1].strip())
    if cur:
        rules.append(cur)
    for r in rules:
        r.setdefault("replace", "")
    return rules


def _yaml_scalar(s: str) -> str:
    """處理 'xxx' / "xxx" / | 三種寫法裡我們實際會用到的部分。"""
    s = s.strip()
    if s.startswith("'") and s.endswith("'") and len(s) >= 2:
        return s[1:-1].replace("''", "'")
    if s.startswith('"') and s.endswith('"') and len(s) >= 2:
        return s[1:-1].encode().decode("unicode_escape")
    return s


def strip_markdown(md: str) -> str:
    """把 markdown 攤平成純中文段落，保留章節標題的文字。"""
    out = []
    for line in md.splitlines():
        s = line.rstrip()
        # 章節標題：留文字，補句號讓語調收尾（沒有 SSML，只能這樣製造停頓）
        m = re.match(r"^#{1,6}\s+(.*)$", s)
        if m:
            text = m.group(1).strip().rstrip("。.:：")
            out.append("")
            out.append(text + "。")
            out.append("")
            continue
        # 有序清單：保留序號的語意
        m = re.match(r"^\s*(\d+)[.)]\s+(.*)$", s)
        if m:
            out.append(f"第{m.group(1)}點，{m.group(2)}")
            continue
        # 無序清單：去掉符號即可，由段落停頓分隔。
        # 必須一次剝掉「多層」——TipTap 的巢狀清單被壓平成 markdown 後長這樣：
        #   「- - t(4;14)：高風險」
        # 只剝一層會留下一個孤兒 dash，唸出來是「減號」。
        m = re.match(r"^\s*(?:[-*+]\s+)+(.*)$", s)
        if m:
            out.append(m.group(1))
            continue
        # 水平線
        if re.match(r"^\s*([-*_])\1{2,}\s*$", s):
            out.append("")
            continue
        out.append(s)
    return "\n".join(out)


def apply_lexicon(text: str, rules: list[dict], show_diff: bool = False) -> tuple[str, list[str]]:
    log = []
    for r in rules:
        try:
            new, n = re.subn(r["pattern"], r["replace"], text, flags=re.M)
        except re.error as e:
            sys.exit(f"lexicon 規則有誤: {r['pattern']!r} → {e}")
        if n:
            log.append(f"{r['pattern']} ×{n}")
            if show_diff:
                print(f"    {r['pattern']!r} → {r['replace']!r}  ×{n}", file=sys.stderr)
        text = new
    return text, log


def collapse(text: str) -> str:
    """收斂空行，讓「段落」這個概念在 synth.py 那邊是可靠的切分依據。"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text)]
    return "\n\n".join(p for p in paras if p)


def normalize_one(path: Path, rules: list[dict], show_diff: bool = False) -> dict:
    src = path.read_text(encoding="utf-8")
    lines = src.splitlines()
    if len(lines) < 2:
        sys.exit(f"{path} 太短 —— 第一行要是集號、第二行要是標題")

    episode, title = lines[0].strip(), lines[1].strip()
    if not re.match(r"^第[〇一二三四五六七八九]+年\s+第[〇一二三四五六七八九十百零]+集$", episode):
        sys.exit(f"{path} 第一行不是集號格式（收到: {episode!r}）")

    body = "\n".join(lines[2:])
    # 圍欄要先處理：它裡面的箭頭與縮排若先被 lexicon 或 markdown 剝除動過，
    # 就再也還原不出原本的流程結構了
    body, fence_log = expand_fences(body)
    body, table_log = expand_tables(body)
    table_log = fence_log + table_log
    body = strip_markdown(body)
    body, lex_log = apply_lexicon(body, rules, show_diff)

    # 開場白 = 集號 + 標題。標題本身也要過 lexicon（它常含 t(4;14) 這類記號）。
    spoken_title, _ = apply_lexicon(title.split(":", 1)[-1].split("：", 1)[-1].strip(), rules)
    head = f"{episode.replace(' ', '，')}。\n\n{spoken_title}。"

    speech = collapse(f"{head}\n\n{body}")
    dest = BUILD / f"{path.stem}.speech.txt"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(speech + "\n", encoding="utf-8")

    chars = len(re.sub(r"\s", "", speech))
    return {
        "qid": path.stem,
        "title": title,
        "episode": episode,
        "speech_path": str(dest.relative_to(ROOT)),
        "sha256": hashlib.sha256(speech.encode("utf-8")).hexdigest(),
        "chars": chars,
        # 實測 zh-TW-YunJheNeural +20% 是 7.2 字/秒（114-053：2891 字 → 402 秒）。
        # 只是給人看的估計值，不影響流程。
        "est_seconds": round(chars / 7.2),
        "tables": table_log,
        "lexicon_hits": len(lex_log),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--diff", action="store_true", help="印出每條 lexicon 規則命中了幾次")
    ap.add_argument("--json", action="store_true", help="輸出 JSON（給 CI 用）")
    args = ap.parse_args()

    rules = load_lexicon()
    results = []
    for f in args.files:
        if args.diff:
            print(f"── {f.name}", file=sys.stderr)
        r = normalize_one(f, rules, args.diff)
        results.append(r)
        if not args.json:
            mins = r["est_seconds"] // 60
            # 這個計數包含表格與 ASCII 流程圖，所以不能叫「表格」
            tbl = f" · {len(r['tables'])} 個結構區塊" if r["tables"] else ""
            print(f"✅ {r['qid']}  {r['chars']} 字 ≈ {mins} 分{tbl}  → {r['speech_path']}")
            for t in r["tables"]:
                print(f"     · {t}")

    if args.json:
        print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
