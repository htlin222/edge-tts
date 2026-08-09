.DEFAULT_GOAL := help
SHELL := /bin/bash

VOICE ?= zh-TW-YunJheNeural
RATE  ?= +20%
YEAR  ?= 114
Q     ?=

help:  ## 顯示這份說明
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup:  ## 建立虛擬環境並安裝依賴
	uv sync

export:  ## 從 MCQ 匯出綜論到 raw/（需要 .env，只在本機跑）  make export YEAR=114
	uv run python scripts/export_notes.py $(YEAR)

export-one:  ## 只匯出一題  make export-one YEAR=114 Q=31
	uv run python scripts/export_notes.py $(YEAR) --only $(Q)

norm:  ## 產生朗讀稿到 build/  make norm Q=114-031（Q 留白=全部）
	@if [ -n "$(Q)" ]; then \
		uv run python scripts/normalize.py raw/$(Q).txt --diff; \
	else \
		uv run python scripts/normalize.py raw/*.txt; \
	fi

one:  ## 完整跑一集：正規化 + 合成 + 試聽  make one Q=114-031
	@test -n "$(Q)" || { echo "用法: make one Q=114-031"; exit 1; }
	uv run python scripts/normalize.py raw/$(Q).txt --diff
	uv run python scripts/synth.py $(Q) --voice "$(VOICE)" --rate "$(RATE)"
	@echo "→ dist/$(Q).mp3"

preview:  ## 只合成開頭 800 字，20 秒內聽到效果  make preview Q=114-031
	@test -n "$(Q)" || { echo "用法: make preview Q=114-031"; exit 1; }
	uv run python scripts/normalize.py raw/$(Q).txt >/dev/null
	@mkdir -p build
	@head -c 2400 build/$(Q).speech.txt > build/$(Q)-preview.speech.txt
	@cd . && uv run python -c "import shutil;shutil.copy('build/$(Q)-preview.speech.txt','build/preview.speech.txt')"
	uv run python scripts/synth.py preview --voice "$(VOICE)" --rate "$(RATE)"
	@echo "→ dist/preview.mp3"

plan:  ## 印出「現在 push 的話 CI 會做什麼」，不實際動作
	uv run python scripts/changed.py --all 2>&1 >/dev/null

feed:  ## 本機重建 site/feed.xml 與索引頁
	uv run python scripts/feed.py --repo htlin222/edge-tts

voices:  ## 列出可用的 zh-TW 語音
	uv run edge-tts --list-voices | grep zh-TW

DEST ?= $(HOME)/Downloads/mcq-tts

sync:  ## 把 release 上的 mp3 同步下來  make sync [DEST=~/Downloads/mcq-tts]
	@mkdir -p "$(DEST)"
	@have=0; got=0; \
	for t in $$(gh api repos/htlin222/edge-tts/releases --paginate --jq '.[].tag_name' | sort); do \
		if [ -f "$(DEST)/$$t.mp3" ]; then have=$$((have+1)); continue; fi; \
		if gh release download "$$t" -p '*.mp3' -D "$(DEST)" 2>/dev/null; then \
			got=$$((got+1)); echo "  ↓ $$t"; \
		else \
			echo "  ⚠ $$t 沒有 mp3（可能還在合成）"; \
		fi; \
	done; \
	echo "✅ 新下載 $$got 集，已存在 $$have 集 → $(DEST)"; \
	echo "   總計 $$(ls "$(DEST)"/*.mp3 2>/dev/null | wc -l) 個檔案，$$(du -sh "$(DEST)" 2>/dev/null | cut -f1)"

REMOTE ?= goanna:~/Downloads

push-remote:  ## 把已同步的 mp3 rsync 到遠端  make push-remote [REMOTE=goanna:~/Downloads]
	@test -d "$(DEST)" || { echo "本機還沒有音檔，先跑 make sync"; exit 1; }
	@echo "→ $(REMOTE)"
	rsync -avh --partial --info=progress2 \
		--include='*.mp3' --exclude='*' \
		"$(DEST)/" "$(REMOTE)/"

ship:  ## 一次做完：從 release 同步下來，再 rsync 到遠端
	@$(MAKE) sync
	@$(MAKE) push-remote

clean:  ## 清掉合成產物（保留朗讀稿與 manifest）
	rm -rf dist

.PHONY: help setup export export-one norm one preview plan feed voices sync push-remote ship clean
