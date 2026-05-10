// Content script entry point.
// S6 will: track text selection + collect highlights into chrome.storage per tab.

import { MSG_EXTRACT } from "../shared/messages.js";
import type { ExtractResponse } from "../shared/messages.js";
import { extractPage } from "./extract.js";
import { extractPubMedMetadata, isPubMedUrl } from "./pubmedDetector.js";

console.log("[News Coo] content script ready");

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if ((msg as { type?: string }).type !== MSG_EXTRACT) return false;

  const respond = sendResponse as (r: ExtractResponse) => void;
  try {
    const page = extractPage(document, location.href);
    if (isPubMedUrl(location.href)) {
      page.pubmed = extractPubMedMetadata(document);
    }
    respond({ ok: true, page });
  } catch (err) {
    respond({ ok: false, error: String(err) });
  }

  return true;
});
