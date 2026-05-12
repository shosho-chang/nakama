import { loadHandle, verifyHandle } from "../vault/handle.js";
import {
  checkSlugExists,
  writePageToVault,
} from "../vault/writer.js";
import type { WritePageOptions, WriteResult } from "../vault/writer.js";
import type { Highlight } from "../vault/frontmatter.js";
import { slugify } from "../vault/slug.js";
import { MSG_EXTRACT } from "../shared/messages.js";
import type { ExtractResponse } from "../shared/messages.js";
import type { ExtractedPage } from "../shared/types.js";
import { getHighlights, clearHighlights } from "../shared/highlights.js";
import { t } from "../i18n/locale.js";
import { configureErrorPanel } from "./errorView.js";

export interface PopupDeps {
  loadHandle: () => Promise<FileSystemDirectoryHandle | null>;
  verifyHandle: (h: FileSystemDirectoryHandle) => Promise<boolean>;
  sendExtract: (tabId: number) => Promise<ExtractResponse>;
  checkSlugExists: (root: FileSystemDirectoryHandle, slug: string) => Promise<boolean>;
  // Single page-aware writer — handles image fetching (default on), frontmatter,
  // highlights, and dedup mode (exact vs auto-suffix) in one call.
  writePage: (
    root: FileSystemDirectoryHandle,
    page: ExtractedPage,
    opts: WritePageOptions,
  ) => Promise<WriteResult>;
  slugify: (title: string) => string;
  getHighlights: (tabId: number) => Promise<Highlight[]>;
  clearHighlights: (tabId: number) => Promise<void>;
}

// DOM helpers — safe to call even in test contexts where elements are pre-built.
function el<T extends HTMLElement>(id: string): T {
  return document.getElementById(id) as T;
}

function showOnly(visibleId: string): void {
  for (const id of [
    "state-loading",
    "state-preview",
    "state-dedup",
    "state-saving",
    "state-saved",
    "state-error",
  ]) {
    const section = el<HTMLElement>(id);
    if (section) section.hidden = id !== visibleId;
  }
}

function makeWriteOpts(
  title: string,
  author: string,
  selectionOnly: boolean,
  highlights: Highlight[],
  exact: boolean,
): WritePageOptions {
  return {
    exact,
    titleOverride: title,
    authorOverride: author,
    highlights,
    frontmatterOpts: {
      selectionOnly,
      extractionMethod: selectionOnly ? "selection" : "defuddle",
    },
  };
}

export async function initPopup(deps: PopupDeps): Promise<void> {
  showOnly("state-loading");

  // Load vault handle.
  const handle = await deps.loadHandle();
  if (!handle) {
    configureErrorPanel(t("noVaultSelected"), () => void chrome.runtime.openOptionsPage());
    showOnly("state-error");
    return;
  }

  const ok = await deps.verifyHandle(handle);
  if (!ok) {
    configureErrorPanel(t("vaultPermissionRevoked"), () => void chrome.runtime.openOptionsPage());
    showOnly("state-error");
    return;
  }

  // Query active tab and extract.
  let tabId: number;
  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    const tab = tabs[0];
    if (!tab?.id) throw new Error("no active tab");
    tabId = tab.id;
  } catch {
    configureErrorPanel(t("noActiveTab"), () => void initPopup(deps));
    showOnly("state-error");
    return;
  }

  let response: ExtractResponse;
  try {
    response = await deps.sendExtract(tabId);
  } catch {
    configureErrorPanel(t("pageUnreachable"), () => void initPopup(deps));
    showOnly("state-error");
    return;
  }

  if (!response.ok) {
    configureErrorPanel(t("extractionFailed", response.error), () => void initPopup(deps));
    showOnly("state-error");
    return;
  }

  const page = response.page;
  const selectionOnly = response.selectionOnly;

  // Read accumulated highlights for this tab.
  const highlights = await deps.getHighlights(tabId);

  // Populate preview form.
  el<HTMLInputElement>("field-title").value = page.title;
  el<HTMLInputElement>("field-author").value = page.author;
  el<HTMLInputElement>("field-site").value = page.site ?? new URL(page.url).hostname;

  const wcEl = el<HTMLSpanElement>("field-word-count");
  if (page.wordCount) wcEl.textContent = t("wordCount", String(page.wordCount));

  const icEl = el<HTMLSpanElement>("field-image-count");
  if (page.imageRefs.length > 0) {
    const key = page.imageRefs.length === 1 ? "imageCount" : "imageCountPlural";
    icEl.textContent = t(key, String(page.imageRefs.length));
  }

  const hcEl = el<HTMLSpanElement>("field-highlight-count");
  if (hcEl) {
    if (highlights.length > 0) {
      const key = highlights.length === 1 ? "highlightCount" : "highlightCountPlural";
      hcEl.textContent = t(key, String(highlights.length));
      hcEl.hidden = false;
    }
  }

  const badgeEl = el<HTMLSpanElement>("badge-selection-only");
  if (badgeEl) badgeEl.hidden = !selectionOnly;

  // Live slug computation.
  const slugEl = el<HTMLElement>("field-slug");
  const dedupWarnEl = el<HTMLParagraphElement>("dedup-warning");

  async function updateSlug(): Promise<void> {
    const title = el<HTMLInputElement>("field-title").value;
    const slug = deps.slugify(title);
    slugEl.textContent = `${slug}.md`;
    const exists = await deps.checkSlugExists(handle!, slug);
    dedupWarnEl.hidden = !exists;
  }

  await updateSlug();
  el<HTMLInputElement>("field-title").addEventListener("input", () =>
    void updateSlug(),
  );

  showOnly("state-preview");

  // Save handler.
  el<HTMLFormElement>("form-preview").addEventListener("submit", (e) => {
    e.preventDefault();
    void handleSave(page, handle, tabId, selectionOnly, highlights, deps);
  });
}

async function handleSave(
  page: ExtractedPage,
  handle: FileSystemDirectoryHandle,
  tabId: number,
  selectionOnly: boolean,
  highlights: Highlight[],
  deps: PopupDeps,
): Promise<void> {
  const title = el<HTMLInputElement>("field-title").value;
  const author = el<HTMLInputElement>("field-author").value;
  const slug = deps.slugify(title);
  const baseOpts = (exact: boolean) =>
    makeWriteOpts(title, author, selectionOnly, highlights, exact);

  const exists = await deps.checkSlugExists(handle, slug);
  if (exists) {
    el<HTMLElement>("dedup-existing-path").textContent = `${slug}.md`;
    showOnly("state-dedup");

    el<HTMLButtonElement>("btn-overwrite").onclick = () =>
      void doWrite(deps.writePage(handle, page, baseOpts(true)), tabId, deps);

    el<HTMLButtonElement>("btn-suffix").onclick = () =>
      void doWrite(deps.writePage(handle, page, baseOpts(false)), tabId, deps);

    el<HTMLButtonElement>("btn-cancel-dedup").onclick = () =>
      showOnly("state-preview");

    return;
  }

  void doWrite(deps.writePage(handle, page, baseOpts(true)), tabId, deps);
}

async function doWrite(
  writePromise: Promise<WriteResult>,
  tabId: number,
  deps: PopupDeps,
): Promise<void> {
  showOnly("state-saving");
  try {
    const result = await writePromise;
    await deps.clearHighlights(tabId);
    el<HTMLElement>("saved-path").textContent = result.path;
    showOnly("state-saved");
  } catch (err) {
    configureErrorPanel(t("writeFailed", String(err)), () => showOnly("state-preview"));
    showOnly("state-error");
  }
}

// Entry point: only auto-runs in extension context.
if (typeof chrome !== "undefined" && typeof chrome.tabs !== "undefined") {
  const realDeps: PopupDeps = {
    loadHandle,
    verifyHandle,
    sendExtract: (tabId) =>
      chrome.tabs.sendMessage(tabId, { type: MSG_EXTRACT }),
    checkSlugExists,
    writePage: writePageToVault,
    slugify,
    getHighlights,
    clearHighlights,
  };
  void initPopup(realDeps);
}
