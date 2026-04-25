---
name: Skill 腳手架四個常踩坑（PR #133 ultrareview 收）
description: 開新 .claude/skills/* 時容易踩、reviewer 才抓到的四類靜默 bug
type: feedback
originSessionId: 30872c1e-80cd-4a8a-ac1b-11865e5cf671
---
開 `.claude/skills/<hyphenated-name>/scripts/<file>.py` + 文件型 capability card 時，
PR #133 ultrareview 一次抓到四個獨立但類別清晰的坑。每一個都是「測試會綠、人讀也看不出、
但 production 路徑就是壞」。先把這些寫進腦袋避免重做。

**1. `python -m` 對 `.claude/skills/<hyphenated>/...` 路徑永遠失效**

三重違法：點起頭 module name 不允許、hyphen 不是合法 identifier、`.claude/` 沒
`__init__.py`。SKILL.md 文件不要寫 `python -m .claude.skills.foo-bar.scripts.x`，
那命令在任何 Python 版本都跑不起來。改成：直接 `python .claude/skills/foo-bar/scripts/x.py`，
**並且**在 script 頂端加 sys.path shim：

```python
_REPO_ROOT = Path(__file__).resolve().parents[N]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
```

否則 `python script.py` 會 `ModuleNotFoundError: shared` —— `sys.path[0]` 是 script
所在目錄，不是 cwd 也不是 repo root。`scripts/run_keyword_research.py:27` 是現成範例。
測試套件用 `importlib` 載入跳過這個問題、`pyproject.toml` `pythonpath = ["."]` 也只在
pytest 生效，所以 CLI 一定要靠 shim 自救。

**Why:** 測試和 LLM 文件兩條路徑都過不到這個失敗 mode；只有 LLM 真的照 SKILL.md 跑
CLI 時才炸，而那已經是 production。

**How to apply:** 開新 skill 第一件事就是寫 shim + 寫一個 `python <path> --help` smoke
（即使是 `cd /tmp && env -u PYTHONPATH python <abs path> --help`）確認從任何 cwd 都能跑。

---

**2. Markdown nested 3-backtick fence 在 capability card 一定壞**

CommonMark 規則：closing fence 至少跟 opener 等長即可閉合，info string 不影響。
所以外層 ` ```markdown` 包內層 ` ```json` 時，內層的 closing ` ``` ` 會被當外層 closer
吃掉，例子裡的 heading / bullets 變成真實文檔結構。**外層用 4 backtick** 是 canonical
解法：

````markdown
```json
{ ... }
```
````

**Why:** 渲染壞掉的 capability card 就是「對外承諾的 contract」失效，downstream 看不
到正確的 output 範例。

**How to apply:** 任何 capability card / SKILL.md 範例需要嵌套 fenced code block 時，
外層直接 4 backtick。GitHub / VSCode preview 都吃這個。

---

**3. 測試注入 `now_fn`（或任何 clock）時，**所有**時間依賴 call site 都要用它**

PR #133 `enrich()` 接 `now_fn` 參數、forward 給 `_effective_end_date` 和
`_output_filename` 都對，但呼叫 `build_seo_context` 時硬寫了
`now_fn=lambda: datetime.now(tz=timezone.utc)`，把外部注入的 clock 影子掉。
結果：filename 和 GSC window deterministic，但 `generated_at` 還是 wall-clock UTC。
測試只 assert filename + window，沒 assert `generated_at`，所以全綠。

**Why:** time injection 的點是「caller 控制全部時間派生值」；漏一個 call site 就破壞
contract，replay/audit 拿到的 `generated_at` 是 stale 的。

**How to apply:** 加 `now_fn` 參數時，grep 全文 `datetime.now`、`time.time`、
`date.today` 把每個都改成 `now_fn() if now_fn else default`。**測試要 assert
generated_at / created_at 等所有時間欄位**，不要只看 filename。

---

**4. `(x or fallback)` 短路守衛在 list-comp / and-chain 裡會默默失效**

破壞範例：
```python
matched = [r for r in rows
           if (r.get("keys") or [""])           # 看似 fallback
           and isinstance(r["keys"][0], str)    # ← 直接索引原 dict，不是 fallback
           and r["keys"][0].strip() == target]
```

`(r.get("keys") or [""])` 回傳 `[""]` 是 truthy，但**值被丟棄** —— 下一個 clause
直接 `r["keys"][0]`，缺 key/None/[] 一律 `KeyError`/`TypeError`/`IndexError`。
正解是 bind 到 local var：

```python
matched = []
for r in rows:
    keys = r.get("keys") or []
    if not keys or not isinstance(keys[0], str):
        continue
    ...
```

`shared/seo_enrich/cannibalization.py` 和 `_select_related_metrics` 都是對的 pattern；
list-comp 裡寫多 clause and-chain 容易誤用。

**Why:** 守衛看似存在實則不存在，code review 一秒過、production GSC 多半正常，但
malformed row 進來就崩，contract 「skip malformed」也是說謊。

**How to apply:** defensive guard 不要寫在 list comprehension 的 condition 裡；改用
explicit for-loop + bind to local var。grep 自家 code 還有沒有同類 pattern。
