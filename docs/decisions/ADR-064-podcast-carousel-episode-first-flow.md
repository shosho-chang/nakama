# ADR-064: Podcast Carousel 採獨立 episode-first flow

**Date:** 2026-08-18
**Status:** Accepted
**Amends:** ADR-014 RepurposeEngine Plug-in Interface

## Decision chronology clarification (2026-08-19)

The early workshop preference for an Instagram-first 1080x1350 canvas is superseded. Since the v15 cross-platform decision, the canonical master is 1080x1080 because the same artifact serves Instagram and YouTube Community. `PodcastCarouselRender.html`, the immutable Template Snapshot, render receipts, fit coordinates, and Review Gate editor must all use that square contract; a future 4:5 derivative would be a separate downstream asset, not a silent change to this flow.

The Review Gate now supports deterministic per-page text layout overrides in addition to the legacy cover-cutout controls. This does not broaden the social-post schema: only the Podcast Episode role/region registry is editable, and future book or health-information designs still fork from this proven flow.

**Contract update (2026-08-18):** 本 ADR workshop 早期允許 Re-hook 的決定，已被同日真實
EP120 review 的使用者決策取代；下列 fixed structure 是目前有效契約。

Podcast Carousel 的 canonical entrypoint 是獨立 `/ig-cards` Skill：從 episode folder 讀乾淨逐字稿與選填 `social_brief.md`，產生 Podcast Carousel Copy Spec、1080×1080 cross-platform square renders 與 Carousel Review Gate。Square master 同時服務 Instagram 與 YouTube community post；不得再把 4:5 當 canonical。它不綁定 ADR-014 的 Blog／FB／IG multi-channel fan-out，也不使用舊 `episode_type` card-count routing。這個偏離是刻意的：Podcast Carousel 有自己的結構、episode-local assets 與 per-card revision workflow；觸發 Carousel 不應重跑或覆蓋其他 channel。

Skill 的可維護規格位於 repo-neutral `skills/ig-cards/`。Claude Code 的 `.claude/skills/ig-cards/` 與 Codex 的 `.agents/skills/ig-cards/` 都是薄入口，只負責 discovery 並要求讀取同一 canonical `SKILL.md`；不得各自複製 workflow。

## Considered Options

- 延伸既有 `IGRenderer`：否決；輸入是 diarized SRT，schema 與固定 5/7/10 card routing 均不符合 Podcast Carousel contract。
- 重做整個 generic Social Post framework：延後；先讓 Podcast Carousel tracer bullet 跑順，其他 Social Post 再從已驗證的 flow fork。

## Consequences

- ADR-014 的 protocol 可以保留供既有 multi-channel flow 使用，但不是 Podcast Carousel v1 的 canonical entrypoint。
- Podcast Carousel 的 copy、render、review 可以獨立重跑，不影響 Blog／FB artifacts。
- 所有產物保存於 `<episode>/ig-carousel/`，與 `packaging/` 同層；可讀取 `packaging/cutouts/`，但不將獨立 Carousel asset 塞回 packaging。
- 每輪只 render 一份主版本；進 render 前由 IG Audience、Episode Editorial、Brand and Evidence 三個獨立 lens 盲審，再由主 agent 對照逐字稿查證與收斂。
- Podcast Carousel v1 固定為 `cover → one hook → ordered points → quote → CTA`；不接受 Re-hook。主要 Hook 必須能統攝後續多個受眾感興趣的訪談重點。
- Canonical skill 由當前 Codex 或 Claude Code agent 執行，不呼叫外部 LLM API 或隱性 provider；三個 review lens 只透過獨立 subagents 執行。
- Review Gate 的非空 feedback 建立 agent-neutral correction job；當前 E2E agent 只有在 source current／artifact receipts 完整時才能 claim，之後產生新 revision 並回報 progress。有效 claim lease 期間不得被另一 executor 搶走；lease 過期後才能 reclaim，progress 會續租。沒有相容 executor 在線時保持 `queued`。
- 建立／查詢 correction job 只是 file-state handoff，不是 dispatcher。Review Gate 顯示 job ID，使用者需回到目前執行該 episode 的 Codex／Claude Code task 讓 agent 明確 claim。
- Review Gate 全空才可整份 Approve；Approve 不修改內容、不啟動 correction，也不發布。
- Review Gate 的卡片 editor 可直接修改 allowlisted display copy；cover 另可提交 deterministic layout override。這些值只進入 revision-bound correction job，必須由 executor 產生新版、保存三個不同 subagent 的 review receipts、生成 matching converged panel 並完成全尺寸 visual QA。Completion 會驗 current result manifest／Copy Spec／PNG receipts 與 structured exact diff，不得把使用者編輯或 progress label 視為自動 approved。
- Renderer 產生的 review manifest 同時收據化 `render_input.html` 並暴露同一 canonical refit API 給 sandbox editor。舊 manifest 的 receipt 欄位保持 optional 以維持 review 相容性，但不允許以未收據 HTML 開啟 editor。
- 未來的書本與身心健康 social posts 不直接塞入 Podcast Carousel schema。
