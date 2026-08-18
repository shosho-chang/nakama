# ADR-064: Podcast Carousel 採獨立 episode-first flow

**Date:** 2026-08-18  
**Status:** Accepted  
**Amends:** ADR-014 RepurposeEngine Plug-in Interface

**Contract update (2026-08-18):** 本 ADR workshop 早期允許 Re-hook 的決定，已被同日真實
EP120 review 的使用者決策取代；下列 fixed structure 是目前有效契約。

Podcast Carousel 的 canonical entrypoint 是獨立 `/ig-cards` Skill：從 episode folder 讀乾淨逐字稿與選填 `social_brief.md`，產生 Podcast Carousel Copy Spec、1080×1350 renders 與 Carousel Review Gate。它不綁定 ADR-014 的 Blog／FB／IG multi-channel fan-out，也不使用舊 `episode_type` card-count routing。這個偏離是刻意的：Podcast Carousel 有自己的結構、episode-local assets 與 per-card revision workflow；觸發 Carousel 不應重跑或覆蓋其他 channel。

## Considered Options

- 延伸既有 `IGRenderer`：否決；輸入是 diarized SRT，schema 與固定 5/7/10 card routing 均不符合 Podcast Carousel contract。
- 重做整個 generic Social Post framework：延後；先讓 Podcast Carousel tracer bullet 跑順，其他 Social Post 再從已驗證的 flow fork。

## Consequences

- ADR-014 的 protocol 可以保留供既有 multi-channel flow 使用，但不是 Podcast Carousel v1 的 canonical entrypoint。
- Podcast Carousel 的 copy、render、review 可以獨立重跑，不影響 Blog／FB artifacts。
- 所有產物保存於 `<episode>/ig-carousel/`，與 `packaging/` 同層；可讀取 `packaging/cutouts/`，但不將獨立 Carousel asset 塞回 packaging。
- 每輪只 render 一份主版本；進 render 前由 IG Audience、Episode Editorial、Brand and Evidence 三個獨立 lens 盲審，再由主 agent 對照逐字稿查證與收斂。
- Podcast Carousel v1 固定為 `cover → one hook → ordered points → quote → CTA`；不接受 Re-hook。主要 Hook 必須能統攝後續多個受眾感興趣的訪談重點。
- 未來的書本與身心健康 social posts 不直接塞入 Podcast Carousel schema。
