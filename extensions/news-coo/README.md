# News Coo

Browser extension that extracts the main content of a web page (no nav, no
sidebar, no ads), converts it to clean Markdown, downloads any embedded
images, and writes the result directly into the Nakama Obsidian vault under
`Inbox/kb/`.

It is a thin delivery extension — translation, bilingual rendering, and
Reader integration are handled downstream by the Robin agent on the Nakama
backend.

## Install (unpacked)

1. Clone the repo and install dependencies:
   ```bash
   git clone https://github.com/shosho-chang/nakama.git
   cd nakama/extensions/news-coo
   npm install
   npm run build        # outputs to dist/
   ```

2. Open Chrome and navigate to `chrome://extensions`.

3. Enable **Developer mode** (toggle in the top-right corner).

4. Click **Load unpacked** and select the `extensions/news-coo/dist/` folder.

5. The News Coo icon appears in your toolbar. Pin it for quick access.

## First-time setup

1. Click the News Coo icon → an error panel appears: "No vault selected."
2. Click **Retry** — it opens the **Options** page.  
   Alternatively, right-click the icon → **Options**.
3. Click **Pick folder** and select your Obsidian vault root (the folder that
   contains your `.obsidian/` directory).
4. Chrome asks for read/write permission — click **Allow**.
5. The status line shows `Vault: <folder-name>`. You are ready to clip.

## Usage

### Normal clip (with preview)

1. Navigate to any article you want to save.
2. Press **Alt+Shift+N** (or click the extension icon).
3. The popup shows a preview with editable **Title**, **Author**, and **Site**
   fields, plus the auto-generated slug path `Inbox/kb/<slug>.md`.
4. Optionally highlight passages first (Alt+Shift+M per selection) — the
   highlight count appears in the preview badge.
5. Click **Save**. The file is written to your vault. The popup shows the
   final path.

### Quick clip (no preview)

Press **Alt+Shift+Q** from any page. The extension extracts, writes, and
sends a Chrome notification — no popup required.

### Mark a selection as highlight

Select text on a page, then press **Alt+Shift+M**. The selection is stored
in session storage and included in the next clip for that tab.

### Context menu

Right-click any page → **News Coo — Clip this page** to trigger a quick clip
without using the keyboard shortcut.

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| Alt+Shift+N | Open popup (preview before saving) |
| Alt+Shift+Q | Quick-clip directly to vault |
| Alt+Shift+M | Mark selected text as highlight |

To change shortcuts: `chrome://extensions/shortcuts`

## Error states

| Error message | Cause | Resolution |
|---|---|---|
| No vault selected | Extension was just installed or vault was cleared | Open Options → Pick folder |
| Vault permission revoked | Chrome cleared site permissions | Open Options → Pick folder again |
| Could not reach page | Content script not injected (e.g. chrome:// page) | Reload the tab or navigate to a regular URL |
| Extraction failed | Page content is empty or unsupported | Try on a different page |
| Write failed | Disk full, read-only filesystem, or quota exceeded | Free disk space and try again |

## Language support

The popup, options page, and notifications automatically switch to **繁體中文**
when Chrome's UI language is set to `zh-TW` or `zh-Hant`. All other locales
use English.

To switch Chrome's language: `chrome://settings/languages` → Add `Chinese (Traditional)` → Move to top → Relaunch.

## Stack

- Chrome MV3
- TypeScript (strict) + Rolldown
- Vitest + happy-dom
- ESLint (typescript-eslint recommended-type-checked)
- [Defuddle](https://github.com/kepano/defuddle) for extraction (npm dep)
- File System Access API for vault writes

## Local development

```bash
cd extensions/news-coo
npm install
npm run build          # → dist/
npm test               # vitest
npm run test:coverage  # vitest + coverage report (thresholds enforced)
npm run check          # tsc --noEmit
npm run lint           # eslint
```

## Location in monorepo

Lives at `extensions/news-coo/`. History preserved via
`git subtree add` from the original standalone `E:\news-coo` repo on 2026-05-10.

## License

MIT. See `LICENSE` and `NOTICE` for attribution to upstream projects.
