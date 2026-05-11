// Content script entry point.

import { MSG_EXTRACT } from "../shared/messages.js";
import type { ExtractResponse } from "../shared/messages.js";
import { extractPage } from "./extract.js";
import { extractPubMedMetadata, isPubMedUrl } from "./pubmedDetector.js";
import { registerHighlightCollector } from "./highlightCollector.js";

console.log("[News Coo] content script ready");

registerHighlightCollector();

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if ((msg as { type?: string }).type !== MSG_EXTRACT) return false;

  const respond = sendResponse as (r: ExtractResponse) => void;
  try {
    const sel = window.getSelection();
    const hasSelection =
      sel !== null && !sel.isCollapsed && sel.toString().trim().length > 0;

    let selectionHtml: string | undefined;
    if (sel !== null && hasSelection && sel.rangeCount > 0) {
      const fragment = sel.getRangeAt(0).cloneContents();
      const tmp = document.createElement("div");
      tmp.appendChild(fragment);
      selectionHtml = tmp.innerHTML;
    }

    const page = extractPage(document, location.href, selectionHtml);
    if (isPubMedUrl(location.href)) {
      page.pubmed = extractPubMedMetadata(document);
    }
    respond({ ok: true, page, selectionOnly: hasSelection });
  } catch (err) {
    respond({ ok: false, error: String(err) });
  }

  return true;
});
