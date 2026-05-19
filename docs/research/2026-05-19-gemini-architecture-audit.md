# Gemini Audit of Nakama URL Architecture Proposal v1

> Multi-agent panel step 3/5. Dispatched 2026-05-19 via `gemini-2.5-pro` against the v1 proposal (PR #610) and Codex audit (companion file `2026-05-19-codex-architecture-audit.md`).
> Final verdict: **Reject** (proposes task-based `/app/*` alternative).

***

**Auditor:** Gemini
**Date:** 2024-05-21
**Scope:** Second-opinion audit of Claude's URL proposal and Codex's (GPT-5) first-pass audit for the Nakama multi-agent system.

This audit provides a distinct perspective, focusing on areas where my training priors in multilingual contexts, information architecture, and user-centric design differ from those of Claude and Codex. I will acknowledge prior findings and build upon them, rather than repeating them.

### Section 1 — INFORMATION ARCHITECTURE & MENTAL MODEL

Codex correctly audited the proposal against its own internal logic (ADRs, code). I will step back and audit the proposal's core premise: the 4-layer mental model itself. For a single-operator content pipeline, an agent-centric model is a premature and likely incorrect abstraction.

The proposal organizes the UI around the *producers* (Public, Bridge, Brook, Robin), not the *process* or the *user's intent*. This is a common anti-pattern in internal tools, where system architecture is exposed directly in the user interface. A single operator, Zhang Xiuxiu (張修修), does not think, "I need to use the Brook agent now." She thinks, "I need to write a new article," or "I need to review my research." The proposed URL structure forces her to map her tasks onto the system's internal agent names.

Let's compare this to established information architecture (IA) patterns:

1.  **Diátaxis Framework:** This documentation framework organizes content by user need: Tutorials (learning), How-To Guides (doing), Explanations (understanding), and Reference (information). The Nakama proposal is organized by *who* (which agent), not *why* (the user's goal). A Diátaxis-inspired model would yield paths like `/guides/writing-from-source` or `/reference/seo-audits`, which are immediately comprehensible to a new user or future collaborator without needing to learn the agent roster.

2.  **CMS Dashboards (WordPress, Ghost):** These systems organize by *content object* and *action*. A user navigates to `/wp-admin/post-new.php` or `/ghost/#/editor/post`. The URL describes the object (`post`) and the action (`new`, `editor`). The proposed `/brook/writing/{source_id}` describes the agent (`brook`) and a generic action (`writing`). A more canonical pattern would be `/writing/post/{source_id}` or `/drafts/from-source/{source_id}`.

3.  **Pipeline Ops UIs (Airflow, Dagster):** Even in highly technical domains, the UI is organized around the *workflow artifacts*—DAGs, Runs, Tasks, Assets. The URL reflects the operational reality of the pipeline, not the name of the worker executing the task.

The proposed 4-layer model (`Public / Bridge ops / Brook editorial / Robin knowledge`) conflates agent identity with user workflow. While Brook and Robin are distinct agents, their UI surfaces (`--brk-*` token set) represent a single, fluid user experience for the author: research and writing. Separating them into `/brook/*` and `/robin/*` introduces an artificial boundary in the URL space that does not exist in the user's mental model.

A partner or future contributor landing in this URL space would be confused. `/brook/context` means nothing without the institutional knowledge that "Brook is the writing agent" and "context is the package for Claude.ai." A URL like `/tools/prompt-builder` would be self-documenting. The current proposal leaks implementation details and requires an internal glossary to navigate.

### Section 2 — DIFFERENT PRIOR

My training data and reasoning chain differ from Claude's and GPT-5's, particularly regarding internationalization (i18n) and search engine behavior. This reveals several unexamined assumptions in the proposal.

1.  **URL Internationalization (i18n):** The creator is Chinese-language first (張修修, shosho.tw). The proposal hardcodes an entirely English URL structure. While this is common for technical backends, it forecloses future possibilities without consideration. What if shosho.tw expands to include an English-language blog? A robust URL architecture would reserve a path segment for language, e.g., `/app/zh/write` or `/app/en/write`. The current proposal would require another painful, site-wide migration to introduce i18n, whereas planning for it now (even if unused) is trivial. The decision to use English is defensible for developer ergonomics, but it should be an explicit choice, not an unchallenged default.

2.  **Chinese-Language SEO & URL Encoding:** My training includes extensive data on how Chinese-language sites structure URLs for Google and Baidu. There are three common patterns:
    *   **English:** `example.com/en/health/longevity-diet` (Clean, universally safe for copy-paste).
    *   **Pinyin:** `example.com/zh/jiankang/changshou-yinshi` (Readable by Chinese speakers, but can be ambiguous and long).
    *   **UTF-8 Characters:** `example.com/zh/健康/长寿饮食` (Perfectly readable, but prone to ugly percent-encoding when shared, e.g., `.../%E5%81%A5%E5%BA%B7/...`).

    The proposal's choice of English slugs (`/brook/context`) is the most technically stable and avoids the UTF-8 encoding problem. However, this is an implicit decision. For the public-facing `/progress` URL, this is acceptable. But if any content-related URLs were ever made public, the English-only structure would be a distinct choice with SEO implications that have not been discussed. The proposal misses the opportunity to state *why* English is being chosen (universality, encoding stability) and to confirm this aligns with the creator's long-term brand strategy.

3.  **Google Search Ranking Perspective:** The proposal correctly identifies the root `/` as a branding and entry point opportunity. However, it frames this as a cosmetic change. From a search perspective, this is the single most important URL on the domain. Its `<title>`, meta description, and H1 tags will define the site's identity to Google. The migration plan (Phase 1) should include not just "build the page" but "define the site's core search identity." Furthermore, the `/progress` URL, being public, is indexable. Its title and content should be optimized for the intended audience (partners), as a partner might Google "shosho.tw progress" to find it. The current proposal treats these pages as simple routes, not as public-facing digital assets.

### Section 3 — CLAUDE/CODEX BLIND SPOTS

Codex performed an excellent, code-grounded audit, catching route errors and ADR conflicts. However, both Claude and Codex share a bias toward the technical implementation of a URL, overlooking the holistic user experience that surrounds it.

1.  **Page Titles, Breadcrumbs, and Navigation UX:** A URL is not an island. It is reflected in the browser tab's `<title>`, the site's breadcrumb navigation, and the active state of sidebar menus. The proposal to rename `/promotion-review/*` to `/brook/promotion/*` is not just a route change. It implies the page title will change from "Promotion Review" to "Brook Promotion," and the breadcrumb from `Home > Promotion Review` to `Home > Brook > Promotion`. This reinforces the agent-centric model I critiqued in Section 1. Neither Claude's proposal nor Codex's audit mentions the cascading UX impact of these renames. A proper migration plan must include updating all corresponding titles, breadcrumbs, and navigation link labels to ensure a consistent user experience.

2.  **The URL as Identity for Shared Links:** Codex noted the `/progress` URL is partner-facing. This link is likely shared via email, Slack, or other messaging apps. When `https://nakama.shosho.tw/progress` is pasted, the unfurled link preview (driven by Open Graph tags) becomes its identity. The proposal does not specify any changes to the `<meta property="og:title">`, `og:description`, or `og:image` tags. A generic title like "Nakama Progress" is less effective than "Shosho.tw x [Partner Name] | Project Progress." The URL is an API for social sharing, and the proposal ignores this critical function.

3.  **Shared Bias on Renaming `/brook/bridge`:** Both models focused on finding a better *name* (`/brook/context`, `/brook/handoff`). They share the assumption that the URL must live under `/brook`. This reinforces the flawed agent-centric IA. The *function* of this page is to package context for an external tool (Claude.ai). A function-first name would be `/tools/prompt-builder` or `/tools/claude-handoff`, located in a neutral top-level directory. This makes the tool's purpose clear without requiring knowledge of the "Brook" agent.

### Section 4 — i18n / CHINESE-LANGUAGE & PARTNER CONTEXT

This is the area where a different prior provides the most value. The proposal is written as if for a generic Silicon Valley startup, failing to center the creator's specific context.

1.  **English-Only URL "Smell":** For a Chinese-language creator, English-only internal URLs are not necessarily a "smell," but they are a deliberate choice that should be acknowledged. As noted in Section 2, English slugs are often the most practical choice for technical stability. However, the proposal's names are jargon. "Handoff" and "Context" are abstract. A more concrete, bilingual-friendly term might be better. For example, `/tools/copy-for-claude`. More importantly, there is no consideration for aliasing. It would be trivial to add a redirect from a Pinyin or Chinese path, e.g., `/gongju/tishi` (工具/提示 - tools/prompt), to the canonical English URL for the operator's convenience. This costs nothing and improves ergonomics.

2.  **Partner-Facing `/progress` Metadata:** This page *must* have tailored metadata for both `zh-TW` and `en` audiences, even if the page content is primarily in one language. Using `<meta http-equiv="Content-Language" content="en">` and providing `og:locale` tags is crucial. The `og:title` and `og:description` should be crafted to be clear and professional when the link is shared with partners, who may be operating in either language. The proposal completely ignores this presentation layer.

3.  **Brand Identity (修修 / shosho.tw):** The proposal buries the creator's brand under generic agent names. The primary brand is "Shosho," not "Brook" or "Robin." The URL structure should reflect this. Instead of `/brook/projects/{slug}`, a structure like `/app/projects/{slug}` or `/studio/projects/{slug}` would feel like it belongs to the "Shosho" brand, with the agents being an implementation detail working behind the scenes. The current proposal makes it seem like the user is logging into a third-party tool named "Nakama" with components named "Brook" and "Robin," rather than into their own custom-built "Shosho Studio."

### Section 5 — ARCHITECTURAL CONCERNS

Codex identified low-level architectural issues like redirect chains and ADR drift. I will focus on higher-level architectural decisions the proposal locks in.

1.  **Path-as-Permission-Model Lock-in:** The proposal solidifies a `/{agent}/*` structure. This implicitly ties the permission model to the agent. What happens when the owner wants to share a single, specific writing surface (`/brook/writing/{id}`) with a guest collaborator? The permission system would have to grant access to a sub-path of `/brook`, which feels unnatural. If the structure were task-based (`/writing/drafts/{id}`), granting access to `/writing/drafts/{id}` is a more intuitive and granular rule. The agent-based structure creates future friction for collaboration.

2.  **Technical Debt of Agent-Centricity:** The primary technical debt this proposal incurs is not in the code, but in the system's vocabulary. By enshrining agent names in the URLs, every future developer, collaborator, or even the owner herself must continue to think in terms of the system's architecture rather than the content workflow. This is a cognitive tax that will be paid indefinitely. It makes onboarding new people (or even new agents) harder, as the URL structure will constantly need to be explained.

3.  **Migration Risk:** Codex correctly noted the risk in under-specifying the renames. I'll add that Phase 1 (marketing page) and Phase 2 (Brook renames) create a state of maximum inconsistency. For a time, some editorial URLs will be at the root (`/projects/{slug}`) while others are under `/brook`. This is confusing for the single operator. The phases should be reordered to minimize this "in-between" state. All renames for a given workflow should happen atomically in a single phase.

4.  **The Hidden 5th Layer: `/api/*`:** The proposal lists `/api/*` as "cross-cutting." This is an understatement. The API is a distinct architectural layer with its own routing, authentication, and versioning concerns. By not giving it a formal place in the 4-layer model, the proposal risks it becoming a dumping ground for RPC calls that don't fit neatly into the UI structure. It should be explicitly named as Layer 5: The API Layer, forcing a more deliberate design for its endpoints rather than treating them as implementation details of the web UI.

### Section 6 — FINAL VERDICT

**Reject.**

The proposal correctly identifies existing problems but prescribes a solution—an agent-centric information architecture—that introduces more significant, long-term architectural and usability issues. It prioritizes the system's internal model over the user's mental model, fails to account for the creator's specific linguistic and brand context, and overlooks the holistic user experience of a URL.

Codex's audit provides excellent tactical fixes, but applying them would be patching a flawed foundation. A fundamentally different organizing principle is needed.

**Top 5 Recommended Modifications (Constituting a New Proposal):**

1.  **Adopt a Task-Based Organizing Principle.** Abandon the agent-centric prefixes (`/brook`, `/robin`). Reorganize the authenticated application under a single, neutral prefix like `/app` or `/studio`.
    *   **Alternative Principle:** Organize by workflow stage, as Codex suggested, or by content object type. My recommendation is a hybrid:
        *   `/app/research/sources` (replaces `/robin/read`, `/robin/done`)
        *   `/app/research/concepts` (replaces `/robin/concept/*`)
        *   `/app/writing/drafts` (replaces `/projects/{slug}`)
        *   `/app/tools/prompt-builder` (replaces `/brook/bridge` -> `/brook/context`)
        *   `/app/operations/seo-audits` (replaces `/bridge/seo/*`)
        *   `/app/operations/dashboard` (replaces `/bridge`)

2.  **Prioritize Brand and User Experience.** The root `/` should be the "Shosho" brand home. The authenticated `/app` should feel like the "Shosho Studio." All page titles, breadcrumbs, and navigation elements must be redesigned in concert with the URL changes to reflect the task-based model, not the agent names.

3.  **Create a Formal Public Layer Strategy.** Explicitly define the strategy for all public-facing surfaces (`/`, `/progress`). This includes defining their target audience, SEO strategy, and Open Graph/social sharing metadata for both English and Chinese contexts.

4.  **Fix the Inventory and Execute Renames Atomically.** Implement Codex's recommendation #1 to create a correct and comprehensive route inventory. Then, refactor the migration plan to group all related renames into a single, atomic phase to minimize user disruption and logical inconsistency. The cosmetic marketing page can be built at any time and should not block the core architectural cleanup.

5.  **Elevate the API to a First-Class Layer.** Formally recognize `/api/*` as the fifth layer of the architecture. Document its structure and design principles separately from the web UI to ensure it evolves cleanly. This prevents the API from becoming a mere side effect of the frontend's needs.
