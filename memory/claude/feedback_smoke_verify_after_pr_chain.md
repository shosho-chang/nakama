---
name: smoke-verify-after-pr-chain
description: After a multi-PR feature chain validated only by unit tests, run smoke test against production wiring before declaring done
metadata:
  type: feedback
---

After a chain of related PRs in a single session that's been validated only by unit tests, run a smoke test against the **production wiring** before declaring the chain done. Don't skip straight to "next feature PR".

**Why:** Unit tests can pass while wiring is broken — import bug in the wiring module, schema version mismatch, route not registered, dry-run/llm branch never exercised. The 2026-05-27 ADR-034 entity chain (9 PRs across PR2a→PR4) was validated only by unit tests. The smoke test caught nothing concrete that day but the **act of starting the server end-to-end** is what distinguishes "code paths exercised by tests" from "production wiring actually composes." User explicitly validated this option ("用 Playwright 跑") over alternatives to ship more PRs immediately — a quiet endorsement of pause-and-verify discipline.

**How to apply:** When user asks "接下來呢" after a multi-PR feature chain, include "pause + smoke verify" as one of the recommended options — frame it as the **first** option, not a hesitation. Web stack: ask user to start the dev server with `! python -m uvicorn ...` (auto-mode blocks Claude from starting servers in main repo per [[worktree-control-plane]]), then drive Playwright via `mcp__plugin_playwright_playwright__*`. Functional verification bar: clean startup, login, list page, one full `/start` round-trip, on-disk artifact (manifest schema_version, etc.). Visual UX check via fixture-drop is optional but auto-mode blocks Claude from seeding fake data into user's live vault — ask user to copy fixture themselves.

Related: [[use-mcp-browser-for-ui-verify]] (routing UI verify mechanics through Playwright MCP) · [[token-efficient-smoke-testing]] (prefer snapshot over screenshot for token budget) · [[pipeline-anchored-planning]] (similar discipline boundary at the planning stage).
