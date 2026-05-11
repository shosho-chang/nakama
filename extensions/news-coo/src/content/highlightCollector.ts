import { MSG_GET_SELECTION } from "../shared/messages.js";
import type { GetSelectionResponse } from "../shared/messages.js";

export function registerHighlightCollector(): void {
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if ((msg as { type?: string }).type !== MSG_GET_SELECTION) return false;
    const respond = sendResponse as (r: GetSelectionResponse | null) => void;
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) {
      respond(null);
      return false;
    }
    const text = sel.toString().trim();
    if (!text) {
      respond(null);
      return false;
    }
    const offset = sel.getRangeAt(0).startOffset;
    respond({ text, offset });
    return false;
  });
}
