---
name: Transcribe WhisperX iter3 algorithm 2026-04-30
description: 三輪 cue 改進迭代結束，iter3 ship 候選：cue avg 18→12.9、詞邊界 cut 16→0、7/7 acceptance set 全命中；演算法在 shared/transcriber.py HEAD
type: project
created: 2026-04-30
confidence: high
---

WhisperX algorithm 三輪改進，iter3 收斂 v2 (PR #274) 留下的問題：cue 結構過長 / 詞邊界 cut / 英文 compound 被切。

## 演算法狀態

`shared/transcriber.py` HEAD（uncommitted）= iter3 演算法。

**三個改動**：

```python
# 1. soft/hard 雙閾值（iter1 起，iter2 加 hard）
_MAX_SUBTITLE_CHARS = 14    # 常態目標（v2 是 20）
_MAX_SUBTITLE_HARD = 22     # 容許 ASCII 英文 compound overflow（Traveling Village = 17）

# 2. within-segment dedupe（iter1）
def _dedupe_adjacent_repeats(text):
    # CJK 限定（[一-鿿]）2-4 char unit 重複 → 留一份
    # 額外：whitelist 處理「本本尊」這類 1-char doubling bug
    # 例：「花蓮花蓮」→「花蓮」、「不正常人類不正常人類」→「不正常人類」

# 3. boundary cut redistribute（iter2）+ regex search 修 trailing-English（iter3）
_BUF_TRAILING_ASCII_RE = re.compile(r"[A-Za-z][A-Za-z0-9'\- ]*$")  # iter3 修法
def _force_break(text, max_chars, hard_max=None):
    # 當 buf 結尾連續 ASCII 英文 + 下個 token 也是 ASCII → 容許 overflow 到 hard
    # iter3 改用 regex search（不靠 split(" ")[-1]，解中英連寫無空格 case）

def _redistribute_boundary_cuts(cues):
    # post-process：cue 邊界 bigram (~60 個常見詞) / trigram (~6 個) 拼回偵測 → shift char
    # 例：「然 | 後」→「然後」shift 到 next cue
```

## 結果（vs v2 baseline）

| 指標 | v2 (PR #274) | **iter3** | MemoAI |
|---|---|---|---|
| Cue 數 | 1326 | 1836 | 3101 |
| Avg 字數 | 18.0 | **12.9** | 8.0 |
| ≥18 字 cue | 1099 | **20** | 75 |
| 詞被切到 cue 邊界 | 16 | **0** | n/a |
| Hallucination | 0 | **0** | 0 |
| Within-segment 重複 | 2 | **0** | 0 |
| 7 點 acceptance set | 7/7 | **7/7** | 7/7 |
| Wall clock | 1.4 min | **1.18 min** | n/a |

詳細：[docs/research/2026-04-30-transcribe-3-iter-results.md](../../docs/research/2026-04-30-transcribe-3-iter-results.md)

## Iter 演進教訓

**iter1**（cue=12 + dedupe）— 演算法收斂太緊：max=12 容不下英文 compound（"Traveling Village" 17 字），邊界 cut 反增 16→50

**iter2**（soft/hard 14/22 + redistribute）— 結構修好：邊界 cut 50→0，但「Hell Yes」regex 漏 case（buf "以後對我們來說就是個Hell" split(" ")[-1] 拿到整段中英混合，不 match ASCII）

**iter3**（regex `search` 而非 `split`）— 補上「Hell Yes」中英連寫 case，全綠

**通用教訓**：演算法收斂前要設清楚 acceptance set（7 點 keyword），每輪量化驗證避免回歸。

## 工具產出

- `scripts/iter_test.py` — 通用 iteration runner（跑 WhisperX + 量 metrics + 出 markdown 報告 + 比上版 + git diff 自動抓進報告）。可重用框架，下次改 algorithm 還用同一套。

## Follow-up（未做）

1. **`_BIGRAM_REDIST_SET` / `_TRIGRAM_REDIST_SET` 是 hardcoded 字典** — 長期應走 jieba 詞性 / 詞頻過濾自動化，現是 ~60 個 bigram + 6 個 trigram
2. **iter3 ge18 cue 20 處** vs MemoAI 75 — 還能再貼近 MemoAI 密度，但 trade-off 是 ASCII compound 的 hard ceiling
3. **redistribute 不動 timestamp** — shift 1 char 應同步 shift timestamp（誤差 <0.5s 接受，求美主義可補）
4. **「就好生羨慕啊」`生` vs `深`、「數額遊牧」** — ASR 同音字錯，下游 LLM 校正補位
5. **ship 還沒做完整 pipeline 天花板測試**（含 LLM 校正 + Gemini 仲裁）— 修修待做
