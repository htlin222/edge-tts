#!/usr/bin/env python3
"""build/<qid>.speech.txt → dist/<qid>.mp3 + dist/<qid>.vtt

為什麼不直接 `edge-tts --file whole.txt`：
  一集中位 7,200 字、約 24 分鐘。單一 WebSocket 連線撐這麼久，斷線一次就整集重來。
  所以按段落切成數段、逐段合成、逐段重試，最後用 ffmpeg 串起來。
  段落之間插一小段靜音 —— edge-tts 沒有 SSML，這是唯一能製造「換段」感覺的方法。

字幕：edge-tts 吐的是 SRT（有序號、毫秒用逗號）。這裡照收，另外轉一份 VTT，
因為 podcast 播放器與瀏覽器吃的是 VTT。多段串接時要把時間軸依序往後推。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import edge_tts
except ImportError:
    sys.exit("缺少 edge-tts —— 跑 `uv sync` 或 `pip install edge-tts`")

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"

DEFAULT_VOICE = "zh-TW-YunJheNeural"
DEFAULT_RATE = "+0%"
CHUNK_CHARS = 1200      # 每段上限，約 4 分鐘語音
GAP_SECONDS = 0.6       # 段落之間的靜音
MAX_RETRIES = 5
# 一段 1200 字正常只要 10–40 秒。給到 180 秒是為了「卡住」而不是「慢」——
# edge_tts 的 stream() 對伺服器不回應沒有任何保護，會無限等下去。
# 這在 CI 上真的發生過：一個 job 從 17:53 卡到 23:53，撞到 GitHub 的 6 小時上限被砍，
# 整批 10 集全部作廢。沒有這個 timeout，重試邏輯永遠不會被觸發。
CHUNK_TIMEOUT = 180


def chunk_text(text: str, limit: int = CHUNK_CHARS) -> list[str]:
    """按段落累積，超過上限就切。單一段落自己就超長時，退回按句號切。"""
    chunks, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if len(para) > limit:
            for sent in re.split(r"(?<=[。！？])", para):
                if not sent.strip():
                    continue
                if len(cur) + len(sent) > limit and cur:
                    chunks.append(cur.strip())
                    cur = ""
                cur += sent
            continue
        if len(cur) + len(para) > limit and cur:
            chunks.append(cur.strip())
            cur = ""
        cur += ("\n\n" if cur else "") + para
    if cur.strip():
        chunks.append(cur.strip())
    return chunks


async def _stream_once(text: str, voice: str, rate: str, out_mp3: Path) -> str:
    comm = edge_tts.Communicate(text, voice, rate=rate)
    sub = edge_tts.SubMaker()
    with out_mp3.open("wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                # 中文語音回的是 SentenceBoundary（英文才是 WordBoundary）。
                # SubMaker 不允許混用兩種，所以第一個來的決定型別，
                # 之後不同型別的一律丟掉 —— 寧可少幾行字幕，也不要整段炸掉。
                try:
                    sub.feed(chunk)
                except ValueError:
                    pass
    if out_mp3.stat().st_size == 0:
        raise RuntimeError("回傳 0 bytes")
    return sub.get_srt()


async def synth_chunk(text: str, voice: str, rate: str, out_mp3: Path, idx: int) -> str:
    """合成一段，回傳該段的 SRT 原文。逾時或失敗就指數退避重試。

    退避到 MAX_RETRIES=5（2/4/8/16/32 秒）是因為 edge-tts 的
    「No audio was received」是間歇性的服務端故障，不是參數錯誤 ——
    114-060 就是連續 3 次撞上而整集報銷。多等一會兒通常就過了。
    """
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return await asyncio.wait_for(
                _stream_once(text, voice, rate, out_mp3), timeout=CHUNK_TIMEOUT
            )
        except asyncio.TimeoutError:
            last_err = f"超過 {CHUNK_TIMEOUT}s 沒有完成（連線可能卡住）"
        except Exception as e:  # noqa: BLE001 — 端點什麼錯都可能丟（403/斷線/無音訊）
            last_err = f"{type(e).__name__}: {e}"
        if attempt < MAX_RETRIES:
            wait = 2**attempt
            print(f"    段 {idx} 第 {attempt} 次失敗（{last_err}），{wait}s 後重試",
                  file=sys.stderr, flush=True)
            await asyncio.sleep(wait)
    raise RuntimeError(f"段 {idx} 重試 {MAX_RETRIES} 次仍失敗: {last_err}")


def ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def make_silence(path: Path, seconds: float) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "anullsrc=r=24000:cl=mono", "-t", str(seconds),
         "-c:a", "libmp3lame", "-b:a", "48k", str(path)],
        check=True,
    )


def concat(parts: list[Path], dest: Path) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        for p in parts:
            f.write(f"file '{p.resolve()}'\n")
        listfile = f.name
    # 刻意重新編碼而不是 -c copy：mp3 直接串接會讓 muxer 抱怨 dts 非單調遞增
    # （每段各自從 0 開始計時），部分播放器的進度條會因此錯亂。
    # 參數對齊 edge-tts 自己的輸出（24 kHz 單聲道 48 kbps），聽感沒有損失。
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", listfile, "-c:a", "libmp3lame", "-b:a", "48k",
         "-ar", "24000", "-ac", "1", str(dest)],
        check=True,
    )
    Path(listfile).unlink()


def shift_srt(srt: str, offset: float, start_index: int) -> tuple[str, int]:
    """把一段 SRT 的時間軸整體往後推 offset 秒，序號接續。"""
    def ts(m: re.Match) -> str:
        h, mi, s, ms = (int(m.group(i)) for i in range(1, 5))
        total = h * 3600 + mi * 60 + s + ms / 1000 + offset
        h2, rem = divmod(total, 3600)
        m2, s2 = divmod(rem, 60)
        return f"{int(h2):02d}:{int(m2):02d}:{int(s2):02d},{int(round((s2 % 1) * 1000)):03d}"

    out, idx = [], start_index
    for block in re.split(r"\n\s*\n", srt.strip()):
        lines = block.strip().splitlines()
        if len(lines) < 2:
            continue
        if lines[0].strip().isdigit():
            lines = lines[1:]
        timing = re.sub(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})", ts, lines[0])
        out.append(f"{idx}\n{timing}\n" + "\n".join(lines[1:]))
        idx += 1
    return "\n\n".join(out), idx


def srt_to_vtt(srt: str) -> str:
    body = re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", srt)
    return "WEBVTT\n\n" + body + "\n"


def write_id3(mp3: Path, meta: dict) -> None:
    """沒有 ID3 的話，手機播放器會全部顯示成檔名，100 集完全無法分辨。"""
    try:
        from mutagen.id3 import ID3, TALB, TIT2, TPE1, TRCK, ID3NoHeaderError
    except ImportError:
        print("    ⚠️  未安裝 mutagen，略過 ID3 標籤", file=sys.stderr)
        return
    try:
        tags = ID3(mp3)
    except ID3NoHeaderError:
        tags = ID3()
    parts = meta["qid"].split("-")
    year, num = (parts[0], parts[1]) if len(parts) == 2 and parts[1].isdigit() else ("", "0")
    tags["TIT2"] = TIT2(encoding=3, text=f"{meta['qid']} {meta['title']}")
    tags["TALB"] = TALB(encoding=3, text=f"{year} 年 有脈絡的綜論" if year else "有脈絡的綜論")
    tags["TPE1"] = TPE1(encoding=3, text="血專衝衝衝")
    tags["TRCK"] = TRCK(encoding=3, text=str(int(num)))
    tags.save(mp3)


async def synth_one(qid: str, voice: str, rate: str) -> dict:
    speech = BUILD / f"{qid}.speech.txt"
    if not speech.exists():
        sys.exit(f"找不到 {speech} —— 先跑 normalize.py")
    text = speech.read_text(encoding="utf-8")
    title = text.splitlines()[2].strip().rstrip("。") if len(text.splitlines()) > 2 else qid

    chunks = chunk_text(text)
    DIST.mkdir(exist_ok=True)
    print(f"🎙  {qid}  {len(chunks)} 段  voice={voice} rate={rate}", flush=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        silence = tmpdir / "gap.mp3"
        make_silence(silence, GAP_SECONDS)

        parts, srt_all, offset, idx = [], [], 0.0, 1
        for i, chunk in enumerate(chunks, 1):
            part = tmpdir / f"{i:03d}.mp3"
            srt = await synth_chunk(chunk, voice, rate, part, i)
            dur = ffprobe_duration(part)
            shifted, idx = shift_srt(srt, offset, idx)
            if shifted:
                srt_all.append(shifted)
            offset += dur + GAP_SECONDS
            parts.extend([part, silence])
            print(f"    段 {i}/{len(chunks)}  {len(chunk)} 字 → {dur:.1f}s", flush=True)
        parts.pop()  # 最後一段後面不要靜音

        mp3 = DIST / f"{qid}.mp3"
        concat(parts, mp3)

    srt_text = "\n\n".join(srt_all)
    (DIST / f"{qid}.srt").write_text(srt_text + "\n", encoding="utf-8")
    (DIST / f"{qid}.vtt").write_text(srt_to_vtt(srt_text), encoding="utf-8")

    meta = {"qid": qid, "title": title}
    write_id3(mp3, meta)

    duration = ffprobe_duration(mp3)
    result = {
        "qid": qid,
        "title": title,
        "mp3": str(mp3.relative_to(ROOT)),
        "bytes": mp3.stat().st_size,
        "duration": round(duration, 1),
        "voice": voice,
        "rate": rate,
        "chunks": len(chunks),
    }
    print(f"✅ {qid}  {duration/60:.1f} 分鐘  {mp3.stat().st_size/1e6:.1f} MB", flush=True)
    return result


async def main_async(args) -> None:
    results, failed = [], []
    for qid in args.qids:
        try:
            results.append(await synth_one(qid, args.voice, args.rate))
        except Exception as e:  # noqa: BLE001
            print(f"❌ {qid} 合成失敗: {e}", file=sys.stderr, flush=True)
            failed.append({"qid": qid, "error": str(e)})

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"ok": results, "failed": failed}, ensure_ascii=False), encoding="utf-8"
        )
    if failed:
        print(f"\n❌ {len(failed)} 集失敗: {', '.join(f['qid'] for f in failed)}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("qids", nargs="+", help="例如 114-031 114-053")
    ap.add_argument("--voice", default=DEFAULT_VOICE)
    ap.add_argument("--rate", default=DEFAULT_RATE)
    ap.add_argument("--json-out", help="把結果寫成 JSON（給 CI 用）")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
