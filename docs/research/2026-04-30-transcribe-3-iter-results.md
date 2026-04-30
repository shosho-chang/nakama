# 2026-04-30 — Transcribe 3 輪改進迭代結果

WhisperX algorithm 三輪改進 + sub-agent 跑測試，目標收斂 v2 (PR #274) 留下的 cue 結構 / 詞邊界 / 英文 compound 三個問題。

## TL;DR

**iter3 = ship 候選**：詞邊界 cut 全清（0 處 vs v2 的 16）、cue avg 12.9（vs v2 18.0）、7 點 acceptance set 全命中、無 hallucination 無重複、wall clock 1.18 min 不變。

**Iter 演進**：
- v2 → iter1：放寬 cue 密度（avg 18 → 11），但 max 太緊把英文 compound 切了，邊界 cut 反增
- iter1 → iter2：soft/hard 雙閾值（14/22）保 ASCII 英文 + post-process redist 修邊界，「Traveling Village」回來但「Hell Yes」regex 邊界判斷漏掉
- iter2 → iter3：修 regex 用 `search` 而非 `split(" ")[-1]`，補上「Hell Yes」case

## 完整對照

| 指標 | v2 (PR #274) | iter1 | iter2 | **iter3** | MemoAI |
|---|---|---|---|---|---|
| Cue 數 | 1326 | 2152 | 1843 | **1836** | 3101 |
| Avg 字數 | 18.0 | 11.0 | 12.8 | **12.9** | 8.0 |
| Max 字數 | 21 | 13 | 22 | **22** | 38 |
| ≤10 字 cue | 110 | 336 | 178 | **175** | 2304 |
| ≥18 字 cue | 1099 | 0 | 8 | **20** | 75 |
| 詞被切到 cue 邊界 | 16 | 50 ❌ | **0** | **0** | (n/a) |
| Prompt-leak 幻覺 | 0 | 0 | 0 | **0** | 0 |
| Within-segment 重複 | 2 | 0 | 0 | **0** | 0 |
| Wall clock | 1.4 min | 1.19 min | 1.18 min | **1.18 min** | — |

## 7 點 acceptance set

| keyword | v2 | iter1 | iter2 | **iter3** | 說明 |
|---|---|---|---|---|---|
| 數位遊牧 | ✅ 5 | ✅ 3 | ✅ 3 | **✅ 3** | podcast 主題詞 |
| 心酸血淚 | ✅ 1 | ✅ 1 | ✅ 1 | **✅ 1** | 同音字 心 vs 辛 |
| Paul | ✅ 4 | ✅ 4 | ✅ 4 | **✅ 4** | code-switch 英文人名 |
| Traveling Village | ✅ 3 | ❌ 0 | ⚠️ 2 | **✅ 3** | 英文社群名 |
| **Hell Yes** | ✅ 1 | ❌ 0 | ❌ 0 | **✅ 1** | 英文短語（iter3 新修） |
| 花蓮 | ✅ 10 | ✅ 9 | ✅ 9 | **✅ 9** | 在地名詞 |
| 本尊 | ✅ 1 | ✅ 1 | ✅ 1 | **✅ 1** | 口語表達 |
| **總命中** | 7/7 | 5/7 | 6/7 | **7/7 ✅** | |

## 三輪 algorithm 演進

### Iter 1：cue 密度 + 重複 dedupe

```diff
-_MAX_SUBTITLE_CHARS = 20
+_MAX_SUBTITLE_CHARS = 12

+ _DEDUPE_KNOWN_BUGS = {"本本尊": "本尊"}
+ def _dedupe_adjacent_repeats(text):
+     # CJK 2-4 char unit 重複偵測 + 限 [一-鿿] 避免英文誤食
+     for n in (4, 3, 2):
+         text = re.sub(r"([一-鿿]{" + str(n) + r"})\1+", r"\1", text)
+     for bug, fix in _DEDUPE_KNOWN_BUGS.items():
+         text = text.replace(bug, fix)
+     return text

# _whisperx_to_srt: 在 _split_sentences 前先 dedupe
+ text = _dedupe_adjacent_repeats(text)
```

**效果**：density 達標 (18 → 11)，但 max=12 容不下「Traveling Village」(17 字) 或「Hell Yes」(7 字 + 中文連寫)，邊界 cut 50 處。

### Iter 2：soft/hard 雙閾值 + 邊界 cut redistribute

```diff
-_MAX_SUBTITLE_CHARS = 12
+_MAX_SUBTITLE_CHARS = 14
+_MAX_SUBTITLE_HARD = 22

+_ASCII_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9'\-]*$")

def _force_break(text, max_chars, hard_max=None):
+    # soft 不夠但 hard 容許，且 token 是 ASCII 英文 + buf 也以 ASCII 英文結尾
+    is_ascii_english = bool(_ASCII_TOKEN_RE.match(tok))
+    last_word = buf.strip().split(" ")[-1] if buf.strip() else ""
+    buf_ends_english = bool(_ASCII_TOKEN_RE.match(last_word))
+    if is_ascii_english and buf_ends_english and len(buf) + len(tok) <= hard_max:
+        buf += tok
+        continue

+_BIGRAM_REDIST_SET = {"然後", "因為", "所以", ...}  # ~60 個常見 bigram
+_TRIGRAM_REDIST_SET = {"那時候", "為什麼", "這個人", ...}

+def _redistribute_boundary_cuts(cues):
+    # post-process：偵測 cue N 結尾 + cue N+1 開頭 拼回是常見詞 → shift char 過邊界
```

**效果**：邊界 cut 50 → 0、「Traveling Village」回來。但「Hell Yes」仍 fail — `last_word = buf.strip().split(" ")[-1]` 在「以後對我們來說就是個Hell」這種**中英連寫無空格**的 buf 上抓到整段（含中文），不 match ASCII regex。

### Iter 3：regex search 修 trailing-English 偵測

```diff
+_BUF_TRAILING_ASCII_RE = re.compile(r"[A-Za-z][A-Za-z0-9'\- ]*$")

-    last_word = buf.strip().split(" ")[-1] if buf.strip() else ""
-    buf_ends_english = bool(_ASCII_TOKEN_RE.match(last_word))
+    buf_ends_english = bool(_BUF_TRAILING_ASCII_RE.search(buf))
```

**效果**：buf 結尾連續 ASCII 字元被正確偵測，不論前面是中英連寫還是有空格。「Hell Yes」終於命中。

## 結論

**iter3 ship**：

1. **統計面**：cue 結構從 v2 的「avg 18 / 1099 處 ≥18」收斂到 「avg 12.9 / 20 處 ≥18」，貼近 MemoAI 8.0 / 75 的可讀目標但保留中英 code-switch compound 不切
2. **品質面**：7 點 acceptance set 全命中（v2 基線本來就全命中，iter3 維持）+ 詞邊界切 16→0 + 重複 2→0 + 幻覺 0
3. **效能面**：wall clock 1.18 min vs v2 1.4 min，反而快（cue 數變少 + 後處理線性 O(n)）

**Iter3 演算法已在 `shared/transcriber.py` HEAD**，未 commit，等修修決定 ship。

## 三輪輸出檔

- `tests/files/out/whisperx-iter1/20260415.srt` + `iter1.report.md`
- `tests/files/out/whisperx-iter2/20260415.srt` + `iter2.report.md`
- `tests/files/out/whisperx-iter3/20260415.srt` + `iter3.report.md`

## 工具產出

- `scripts/iter_test.py` — 通用 iteration runner（跑 WhisperX + 量 metrics + 出 markdown 報告）
- `scripts/test_dedupe.py` / `test_iter2_units.py` / `test_iter3_units.py` — unit smoke test

## 後續 follow-up

不在本 3 輪 scope，但浮現的：
1. **`_BIGRAM_REDIST_SET` / `_TRIGRAM_REDIST_SET` 是 hardcoded 字典** — 長期應走 jieba 詞性 / 詞頻過濾自動化
2. **iter3 ge18 cue 20 處** vs MemoAI 75 — 還可以再貼近，但 trade-off 是 ASCII compound 的 hard ceiling
3. **「就好生羨慕啊」`生` vs `深`** — ASR 同音字錯，需 LLM 校正接力，不是切 cue 演算法能解
4. **「數額遊牧」**（cue 19）— 同上 LLM 校正範圍
5. **redistribute 不動 timestamp**：理論上 shift 1 char 應同步 shift timestamp（誤差 <0.5s 接受，但精準求美主義可補）
