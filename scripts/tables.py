#!/usr/bin/env python3
"""把 markdown 表格轉成「唸得出來」的句子。

分工（這是刻意的取捨，不是懶惰）:

  ≤3 欄且無合併儲存格 → 純程式逐列展開
      可重現、零成本、零外部依賴。輸出囉嗦但絕不會聽錯。

  >3 欄，或偵測到合併/多層表頭 → 交給 haiku 改寫成散文
      程式化展開一旦超過三個欄位，聽的人會在第四個值出現時就忘記第一個欄名是什麼。
      這種時候寧可讓模型改寫。

haiku 的輸出「不可重現」——同一張表兩次跑會得到不同稿。所以每一次改寫都會寫進
build/table-cache/<sha256>.md 並 commit 回 repo：有快取就直接用，內容沒變就永遠是
同一份稿。這讓 CI 保持確定性，也讓你能直接讀那份稿、發現改壞了就手動修。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "build" / "table-cache"

HAIKU_MODEL = "claude-haiku-4-5-20251001"
MAX_PROGRAMMATIC_COLS = 3

# 一張 markdown 表格：至少一列表頭 + 一列分隔線 + 一列以上內容
TABLE_RE = re.compile(
    r"(?:^\|.*\|[ \t]*\n)"      # 表頭
    r"(?:^\|[\s:|-]+\|[ \t]*\n)"  # 分隔線 |---|:--|
    r"(?:^\|.*\|[ \t]*\n?)+",    # 內容列
    re.M,
)


def split_row(line: str) -> list[str]:
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]


def looks_complex(header: list[str], rows: list[list[str]]) -> str | None:
    """回傳「為什麼算複雜」，不複雜則回 None。"""
    if len(header) > MAX_PROGRAMMATIC_COLS:
        return f"{len(header)} 欄，超過程式化展開的 {MAX_PROGRAMMATIC_COLS} 欄上限"
    if any(not h for h in header):
        return "有空白欄名，可能是多層表頭被壓平"
    if any(len(r) != len(header) for r in rows):
        return "有列的欄數與表頭不符，可能存在合併儲存格"
    if any(any("<br" in c or "\\n" in c for c in r) for r in rows):
        return "儲存格內含換行，展開後會斷句錯亂"
    return None


def render_programmatic(header: list[str], rows: list[list[str]]) -> str:
    """每一列都複述欄名 —— 囉嗦，但邊做事邊聽也不會聽錯。"""
    cols = "、".join(header)
    out = [f"表格，共{len(rows)}列，欄位為：{cols}。"]
    for i, row in enumerate(rows, 1):
        parts = [f"{h} {v}" for h, v in zip(header, row) if v]
        out.append(f"第{i}列：" + "，".join(parts) + "。")
    return "\n".join(out)


def render_with_haiku(table_md: str, reason: str) -> tuple[str | None, str]:
    """把複雜表格交給 haiku 改寫；結果進快取。

    回傳 (改寫後的文字, 來源)。來源要如實區分「快取」與「這次真的呼叫了 haiku」——
    否則 log 會宣稱每次都重新改寫過，而實際上快取才是常態。
    """
    key = hashlib.sha256(table_md.encode("utf-8")).hexdigest()[:16]
    cached = CACHE / f"{key}.md"
    if cached.exists():
        return cached.read_text(encoding="utf-8").strip(), f"快取 {key}"

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(
            f"  ⚠️  表格需要 haiku 改寫（{reason}）但沒有 ANTHROPIC_API_KEY，"
            "退回程式化展開（會比較難聽）",
            file=sys.stderr,
        )
        return None, "無 API key"  # 讓呼叫端 fallback

    prompt = (
        "把下面這張 markdown 表格改寫成適合「用耳朵聽」的中文散文。\n\n"
        "規則：\n"
        "1. 聽的人看不到表格，所以每個數值都必須帶著它的欄位意義出現。\n"
        "2. 不要用「如下表所示」「上表」這類指涉視覺的說法。\n"
        "3. 不要新增表格裡沒有的資訊，不要下結論，不要補充你的醫學知識。\n"
        "4. 保留原本的英文專有名詞與數字，不要翻譯成中文。\n"
        "5. 只輸出改寫後的段落，不要任何前言或說明。\n\n"
        f"表格：\n{table_md}"
    )
    body = json.dumps(
        {
            "model": HAIKU_MODEL,
            "max_tokens": 2000,
            "messages": [{"role": "user", "content": prompt}],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.load(resp)
    text = "".join(b.get("text", "") for b in data.get("content", [])).strip()
    if not text:
        return None, "haiku 回空字串"

    CACHE.mkdir(parents=True, exist_ok=True)
    cached.write_text(
        f"<!-- 由 haiku 改寫，原因：{reason}\n"
        f"     原始表格 sha256[:16] = {key}\n"
        f"     這份稿已 commit，內容不變就不會重新生成。改壞了可直接手動編輯這個檔。 -->\n"
        f"{text}\n",
        encoding="utf-8",
    )
    return text, f"haiku 新改寫 {key}"


# 圍欄程式碼區塊。在這份材料裡它裝的不是程式碼，而是 ASCII 流程圖
# （114-086 的「臨床斷點定位演算法」）—— 箭頭、縮排、分支，全部是視覺結構。
FENCE_RE = re.compile(r"^```[^\n]*\n(.*?)^```[ \t]*$", re.M | re.S)


def expand_fences(md: str) -> tuple[str, list[str]]:
    """把 ASCII 流程圖改寫成唸得出來的敘述。

    這種東西沒有程式化的解法 —— 縮排層級、箭頭方向、Yes/No 分支之間的關係
    是畫出來的，不是標記出來的。所以一律交給 haiku；沒有 API key 就明講「從略」，
    而不是把一堆箭頭和縮排唸出來假裝有內容。
    """
    log: list[str] = []

    def repl(m: re.Match) -> str:
        block = m.group(0)
        inner = m.group(1)
        lines = len(inner.strip().splitlines())
        text, src = render_with_haiku(block, f"ASCII 流程圖，{lines} 行，無法程式化展開")
        if text:
            log.append(f"流程圖({lines} 行) → {src}")
            return text
        log.append(f"⚠️ 流程圖({lines} 行) → 沒有 ANTHROPIC_API_KEY，內容從略")
        return "此處原文有一段流程圖，內容不適合朗讀，請回到原文閱讀。"

    return FENCE_RE.sub(repl, md), log


def expand_tables(md: str) -> tuple[str, list[str]]:
    """把 md 裡所有表格換成朗讀句。回傳 (新文字, 這次做了什麼的紀錄)。"""
    log: list[str] = []

    def repl(m: re.Match) -> str:
        block = m.group(0)
        lines = [ln for ln in block.strip().splitlines() if ln.strip()]
        header = split_row(lines[0])
        rows = [split_row(ln) for ln in lines[2:]]
        reason = looks_complex(header, rows)
        if reason:
            text, src = render_with_haiku(block, reason)
            if text:
                log.append(f"表格({len(header)}欄×{len(rows)}列) → {src}（{reason}）")
                return text
            log.append(f"表格({len(header)}欄×{len(rows)}列) → 程式化展開（{src}）")
        else:
            log.append(f"表格({len(header)}欄×{len(rows)}列) → 程式化展開")
        return render_programmatic(header, rows)

    return TABLE_RE.sub(repl, md), log


if __name__ == "__main__":
    src = Path(sys.argv[1]).read_text(encoding="utf-8") if len(sys.argv) > 1 else sys.stdin.read()
    out, log = expand_tables(src)
    for line in log:
        print(f"  · {line}", file=sys.stderr)
    print(out)
