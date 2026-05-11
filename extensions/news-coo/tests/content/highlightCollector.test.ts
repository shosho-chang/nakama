import { describe, it, expect, vi, beforeEach } from "vitest";
import { registerHighlightCollector } from "../../src/content/highlightCollector.js";
import { MSG_GET_SELECTION } from "../../src/shared/messages.js";

type MessageListener = (
  msg: unknown,
  sender: unknown,
  sendResponse: (r: unknown) => void,
) => boolean | void;

function setupChrome(listener?: MessageListener): {
  addListener: ReturnType<typeof vi.fn>;
} {
  const addListener = vi.fn((fn: MessageListener) => {
    if (listener === undefined) {
      // Store reference for manual calls in tests
      (setupChrome as { _stored?: MessageListener })._stored = fn;
    }
  });
  (globalThis as Record<string, unknown>).chrome = {
    runtime: { onMessage: { addListener } },
  };
  return { addListener };
}

function callStoredListener(
  msg: unknown,
  sendResponse: (r: unknown) => void,
): boolean | void {
  const fn = (setupChrome as { _stored?: MessageListener })._stored;
  if (!fn) throw new Error("no listener registered");
  return fn(msg, {}, sendResponse);
}

describe("registerHighlightCollector", () => {
  beforeEach(() => {
    delete (setupChrome as { _stored?: MessageListener })._stored;
    setupChrome();
    registerHighlightCollector();
  });

  it("registers a runtime.onMessage listener", () => {
    const { addListener } = setupChrome();
    registerHighlightCollector();
    expect(addListener).toHaveBeenCalledOnce();
  });

  it("ignores messages with a different type", () => {
    const sendResponse = vi.fn();
    callStoredListener({ type: "OTHER" }, sendResponse);
    expect(sendResponse).not.toHaveBeenCalled();
  });

  it("returns null when there is no selection", () => {
    // happy-dom default: no selection
    const sendResponse = vi.fn();
    callStoredListener({ type: MSG_GET_SELECTION }, sendResponse);
    expect(sendResponse).toHaveBeenCalledWith(null);
  });

  it("returns selection text and offset when selection exists", () => {
    // Inject a non-collapsed selection into happy-dom
    const p = document.createElement("p");
    p.textContent = "Hello World";
    document.body.appendChild(p);

    const range = document.createRange();
    range.setStart(p.firstChild!, 0);
    range.setEnd(p.firstChild!, 5); // "Hello"
    const sel = window.getSelection()!;
    sel.removeAllRanges();
    sel.addRange(range);

    const sendResponse = vi.fn();
    callStoredListener({ type: MSG_GET_SELECTION }, sendResponse);

    expect(sendResponse).toHaveBeenCalledWith(
      expect.objectContaining({ text: "Hello", offset: 0 }),
    );

    sel.removeAllRanges();
    document.body.removeChild(p);
  });
});
