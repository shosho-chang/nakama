---
name: __init__.py 用 `import X as Y` 會 shadow 掉同名 submodule
description: 在 package __init__.py 把 submodule 內 function 用同名 alias re-export，monkeypatch.setattr("pkg.submodule.attr") 會抓到 function 而不是 module
type: feedback
created: 2026-04-26
originSessionId: 74b1dda0-888a-4fcc-affd-a88547d84b84
---
在 package `__init__.py` 用 `from .submodule import func as submodule` 會讓 `package.submodule` 在 attribute lookup 時解析到那個 function，shadow 掉原本的子模組。`pytest.MonkeyPatch.setattr("package.submodule.attr", ...)` 會炸 `'function' object at package.submodule has no attribute 'attr'`。

**Why:** 2026-04-26 D.2 SEO audit 開發踩到。`shared/seo_audit/__init__.py` 寫 `from shared.seo_audit.llm_review import review as llm_review` 後，`monkeypatch.setattr("shared.seo_audit.llm_review.get_client", ...)` 19 個 test 全炸；錯誤訊息是「'function' object」而不是「module has no attribute」。Python 的 attribute resolution 在這個 lookup chain 上看到 `shared.seo_audit` package 有個 `llm_review` attribute（function），就停下來不去找 submodule 了。

**How to apply:**
- `__init__.py` re-export submodule 內 function 時，用不同名稱 alias（例 `from .llm_review import review as run_llm_review`），或乾脆不 alias 直接 `from .llm_review import review`，讓 module 名稱保持可訪問
- 寫 test 時優先 `monkeypatch.setattr("shared.seo_audit.llm_review.get_client", ...)`，shadowing 會直接炸出來
- 同樣風險也會發生在 `from .X import X` 這種把 submodule 變 class 的場景；遇到「'function' object 沒 attr」/「'class' object 沒 attr」要先想到這個
- 副作用：相同問題會讓 `import package.submodule as pkg_sub` 之類用法在 some Python versions 行為不一致 — 一律避免 alias 與 submodule 同名
