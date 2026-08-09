# 有脈絡的綜論 · 語音版

把血液腫瘤考古題筆記裡的「有脈絡的綜論」用 edge-tts 的 zh-TW 神經語音唸成 podcast。
**一題一集，一集一個 GitHub release。**

訂閱網址（貼進 Pocket Casts / Overcast / Apple Podcasts）：

```
https://htlin222.github.io/edge-tts/feed.xml
```

節目索引頁：<https://htlin222.github.io/edge-tts/>

---

## 你日常只需要做一件事

**把 `.txt` 丟進 `raw/`，push。** 其餘全自動。

```bash
make export YEAR=114        # 從 MCQ 系統把整年的綜論匯出到 raw/
git add raw/ && git commit -m "114 年綜論" && git push
# → CI 只處理這次新增/變動的那幾集，每集建一個 release，更新 RSS
```

## 資料流

```
MCQ API ──export_notes.py──▶ raw/114-053.txt          ← 你唯一要碰的東西
                              第一行：第一一四年 第五十三集
                              第二行：綜論標題
                              第三行起：正文（參考文獻已切除）
                                   │
                    normalize.py   │  lexicon 發音替換 + 表格展開 + markdown 攤平
                                   ▼
                             build/114-053.speech.txt  ← 「唸出來會是什麼」的唯一真相
                                   │                      （commit 進 repo 供你校對）
                       synth.py    │  分段 → edge-tts → 重試 → ffmpeg 串接 → ID3
                                   ▼
                             dist/114-053.mp3 + .vtt + .srt
                                   │
                                   ▼
              GitHub release  tag=114-053   ← 音檔只在這裡，不進 git
                                   │
                        feed.py    ▼
                             GitHub Pages: feed.xml + index.html
```

## 常用指令

```bash
make help                   # 全部指令
make export YEAR=114        # 匯出整年（需要 .env，只在本機跑）
make export-one YEAR=114 Q=31
make one Q=114-031          # 本機完整跑一集，產出 dist/114-031.mp3
make preview Q=114-031      # 只合成開頭 800 字，20 秒內聽到效果
make norm Q=114-031         # 只產朗讀稿，並印出每條 lexicon 規則命中幾次
make plan                   # 印出「現在 push 的話 CI 會做什麼」
make voices                 # 列出可用的 zh-TW 語音
```

## 目錄

| 路徑 | 是什麼 |
|---|---|
| `raw/*.txt` | **你編輯的來源**。第一行集號、第二行標題、之後正文 |
| `dict/lexicon.yaml` | 發音詞典。唸錯了就改這裡，每條規則都寫了為什麼 |
| `build/*.speech.txt` | 朗讀稿（CI 產生並 commit 回來）。想知道實際唸什麼就讀它 |
| `build/manifest.json` | 每集的 sha256 / 時長 / 大小 / 發佈狀態 |
| `build/table-cache/` | haiku 改寫過的表格稿，有快取就不重跑，改壞了可手動編輯 |
| `site/` | GitHub Pages 內容（feed.xml + 索引頁） |
| `dist/` | 音檔，`.gitignore` 掉了，只存在於 release |

## CI 什麼時候會跑、跑什麼

觸發：push 到 `main` 且動到 `raw/**.txt`，或手動 `workflow_dispatch`。

要做哪幾集，經過**兩層過濾**：

1. **git diff** — 這次 push 動到哪些 `raw/*.txt`
   （首次 push / force push / 淺 clone 取不到 `before` 時自動退回全量）
2. **正規化後的 sha256** — 內容真的變了嗎
   改了 markdown 縮排但朗讀稿一字未變 → 不重新合成。40 分鐘不白花。

接著切成 10 個平行 job，每集：正規化 → 分段合成（每段最多重試 3 次）→ ffmpeg 串接
→ 寫 ID3 → `gh release create <題號>`。

**一集失敗不會拖垮其他集**（`fail-fast: false`）。最後 job 會列出失敗清單並讓 workflow 標紅，
重跑方式：Actions → TTS → Run workflow → `only` 填題號。

`raw/` 裡的檔案被刪除 → 對應的 release 與 tag 一併刪除。

## 唸錯了怎麼辦

改 `dict/lexicon.yaml`，然後：

```bash
make norm Q=114-053    # 看每條規則命中幾次
less build/114-053.speech.txt   # 直接讀朗讀稿確認
```

規則由上而下依序套用，順序有意義（例如中文標點正規化必須排在 `t(4;14)` 之類的
記號規則之後，否則會誤傷分號）。

## 表格怎麼處理

- **≤3 欄且無合併儲存格** → 純程式逐列展開，每列都複述欄名：
  「表格，共五列，欄位為：易位、基因、風險。第一列：易位 t 四 十四，基因 NSD2，風險 高風險。」
  可重現、零成本。
- **>3 欄，或偵測到合併/多層表頭** → 交給 `claude-haiku-4-5` 改寫成散文。
  需要 repo secret `ANTHROPIC_API_KEY`；沒設就退回程式化展開並印警告。
  改寫結果寫進 `build/table-cache/` 並 commit —— **有快取就永遠用同一份稿**，
  這是讓 CI 保持確定性的關鍵（否則同一張表每次跑都會得到不同稿）。

## 已知限制

- **edge-tts 沒有 SSML。** 7.x 已移除任意 SSML 支援，只剩 `--rate/--volume/--pitch`。
  所以沒有 `<break>`、沒有 `<phoneme>`、**沒辦法在中英之間切換語言**。
  「怎麼唸」只能靠送進 TTS 前的字串替換（就是 `dict/lexicon.yaml`），
  段落停頓只能靠切段後插靜音（`synth.py` 的 `GAP_SECONDS`）。
- **edge-tts 走的是逆向的 Edge 免費端點**，不是官方 API。
  GitHub runner 的 IP 被節流或回 403 的風險真實存在。目前的對策是每段重試 3 次
  （指數退避），仍失敗就跳過該集、保留其他集，最後標紅。
  真的常態性失敗的話，`synth.py` 的合成層可以換成 Azure Speech（需要付費金鑰）。
- **zh-TW 只有三個語音**：`YunJheNeural`（男，本專案預設）、`HsiaoChenNeural`、`HsiaoYuNeural`。
- **英文專有名詞由中文語音唸**，腔調偏硬但可懂。這是已接受的取捨。
- **這是 public repo。** 綜論全文與音檔都會被搜尋引擎索引。

## 內容免責

`raw/` 的內容是**個人讀書筆記**，不是教科書、不是臨床指引，可能包含錯誤與過時資訊。
筆記本身有時會標註「此處未核對原文」之類的誠實標記 —— 那些標記會照樣唸出來，
請把它們當成真的警告。任何臨床決策請回到原始文獻。

## 授權

- `scripts/`、`.github/`、`Makefile` — MIT（見 `LICENSE`）
- `raw/`、`build/`、音檔 — CC BY-NC-SA 4.0（見 `CONTENT-LICENSE.md`）

## 設定

匯出功能需要 `.env`（**已 gitignore，絕不能進這個 public repo**）：

```
MCQ_API_BASE=https://...
MCQ_API_KEY=mcqk_...
MCQ_USER_EMAIL=...
```

到 MCQ 站台的 `/profile` →「MCQ 小測驗金鑰」→ 下載 `.skill`，裡面就有 `.env`。
CI 不需要它 —— `raw/` 已經 commit 進 repo 了。
