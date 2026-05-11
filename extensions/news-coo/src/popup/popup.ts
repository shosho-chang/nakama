import { loadHandle, verifyHandle } from "../vault/handle.js";
import {
  checkSlugExists,
  writeToVault,
  writeToVaultExact,
  buildHighlightsSection,
} from "../vault/writer.js";
import type { WriteResult } from "../vault/writer.js";
import { buildFrontmatter } from "../vault/frontmatter.js";
import type { Highlight } from "../vault/frontmatter.js";
import { slugify } from "../vault/slug.js";
import { MSG_EXTRACT } from "../shared/messages.js";
import type { ExtractResponse } from "../shared/messages.js";
import type { ExtractedPage } from "../shared/types.js";
import { getHighlights, clearHighlights } from "../shared/highlights.js";

export interface PopupDeps {
  loadHandle: () => Promise<FileSystemDirectoryHandle | null>;
  verifyHandle: (h: FileSystemDirectoryHandle) => Promise<boolean>;
  sendExtract: (tabId: number) => Promise<ExtractResponse>;
  checkSlugExists: (root: FileSystemDirectoryHandle, slug: string) => Promise<boolean>;
  writeExact: (root: FileSystemDirectoryHandle, slug: string, content: string) => Promise<WriteResult>;
  writeAutoSuffix: (root: FileSystemDirectoryHandle, slug: string, content: string) => Promise<WriteResult>;
  slugify: (title: string) => string;
  buildContent: (
    page: ExtractedPage,
    title: string,
    author: string,
    selectionOnly: boolean,
    highlights: Highlight[],
  ) => string;
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

function buildPageContent(
  page: ExtractedPage,
  title: string,
  author: string,
  selectionOnly: boolean,
  highlights: Highlight[],
): string {
  const synthetic: ExtractedPage = { ...page, title, author };
  const fm = buildFrontmatter(synthetic, {
    selectionOnly,
    highlights,
    extractionMethod: selectionOnly ? "selection" : "defuddle",
  });
  return fm + "\n" + page.markdown + buildHighlightsSection(highlights);
}

export async function initPopup(deps: PopupDeps): Promise<void> {
  showOnly("state-loading");

  // Load vault handle.
  const handle = await deps.loadHandle();
  if (!handle) {
    el<HTMLParagraphElement>("error-msg").textContent =
      "No vault selected. Open extension options to pick a folder.";
    showOnly("state-error");
    el<HTMLButtonElement>("btn-retry").addEventListener("click", () =>
      void chrome.runtime.openOptionsPage(),
    );
    return;
  }

  const ok = await deps.verifyHandle(handle);
  if (!ok) {
    el<HTMLParagraphElement>("error-msg").textContent =
      "Vault permission revoked. Open extension options to re-pick.";
    showOnly("state-error");
    el<HTMLButtonElement>("btn-retry").addEventListener("click", () =>
      void chrome.runtime.openOptionsPage(),
    );
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
    el<HTMLParagraphElement>("error-msg").textContent =
      "Could not determine active tab.";
    showOnly("state-error");
    el<HTMLButtonElement>("btn-retry").addEventListener("click", () =>
      void initPopup(deps),
    );
    return;
  }

  let response: ExtractResponse;
  try {
    response = await deps.sendExtract(tabId);
  } catch {
    el<HTMLParagraphElement>("error-msg").textContent =
      "Could not reach page. Try reloading the tab.";
    showOnly("state-error");
    el<HTMLButtonElement>("btn-retry").addEventListener("click", () =>
      void initPopup(deps),
    );
    return;
  }

  if (!response.ok) {
    el<HTMLParagraphElement>("error-msg").textContent =
      `Extraction failed: ${response.error}`;
    showOnly("state-error");
    el<HTMLButtonElement>("btn-retry").addEventListener("click", () =>
      void initPopup(deps),
    );
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
  if (page.wordCount) wcEl.textContent = `${page.wordCount} words`;

  const icEl = el<HTMLSpanElement>("field-image-count");
  if (page.imageRefs.length > 0)
    icEl.textContent = `${page.imageRefs.length} image${page.imageRefs.length !== 1 ? "s" : ""}`;

  const hcEl = el<HTMLSpanElement>("field-highlight-count");
  if (hcEl) {
    if (highlights.length > 0) {
      hcEl.textContent = `${highlights.length} highlight${highlights.length !== 1 ? "s" : ""}`;
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
    slugEl.textContent = `Inbox/kb/${slug}.md`;
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
  const content = deps.buildContent(page, title, author, selectionOnly, highlights);

  const exists = await deps.checkSlugExists(handle, slug);
  if (exists) {
    el<HTMLElement>("dedup-existing-path").textContent = `Inbox/kb/${slug}.md`;
    showOnly("state-dedup");

    el<HTMLButtonElement>("btn-overwrite").onclick = () =>
      void doWrite(deps.writeExact(handle, slug, content), tabId, deps);

    el<HTMLButtonElement>("btn-suffix").onclick = () =>
      void doWrite(deps.writeAutoSuffix(handle, slug, content), tabId, deps);

    el<HTMLButtonElement>("btn-cancel-dedup").onclick = () =>
      showOnly("state-preview");

    return;
  }

  void doWrite(deps.writeExact(handle, slug, content), tabId, deps);
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
    el<HTMLParagraphElement>("error-msg").textContent = `Write failed: ${String(err)}`;
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
    writeExact: writeToVaultExact,
    writeAutoSuffix: writeToVault,
    slugify,
    buildContent: buildPageContent,
    getHighlights,
    clearHighlights,
  };
  void initPopup(realDeps);
}
