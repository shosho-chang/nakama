import { describe, it, expect, vi, beforeEach } from "vitest";
import { configureErrorPanel } from "../../src/popup/errorView.js";

function buildDOM(): void {
  document.body.innerHTML = `
    <p id="error-msg"></p>
    <button id="btn-retry">Retry</button>
  `;
}

describe("configureErrorPanel", () => {
  beforeEach(() => {
    buildDOM();
  });

  it("sets the error message text content", () => {
    configureErrorPanel("Something went wrong", vi.fn());
    expect(document.getElementById("error-msg")?.textContent).toBe("Something went wrong");
  });

  it("wires the retry button to call the provided callback", () => {
    const onRetry = vi.fn();
    configureErrorPanel("Oops", onRetry);
    document.getElementById("btn-retry")!.click();
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("replaces a previously set message", () => {
    configureErrorPanel("First error", vi.fn());
    configureErrorPanel("Second error", vi.fn());
    expect(document.getElementById("error-msg")?.textContent).toBe("Second error");
  });

  it("replaces the retry callback on second call", () => {
    const first = vi.fn();
    const second = vi.fn();
    configureErrorPanel("Err A", first);
    configureErrorPanel("Err B", second);
    document.getElementById("btn-retry")!.click();
    expect(second).toHaveBeenCalledOnce();
    expect(first).not.toHaveBeenCalled();
  });

  it("does not throw when error-msg element is absent", () => {
    document.body.innerHTML = `<button id="btn-retry">Retry</button>`;
    expect(() => configureErrorPanel("msg", vi.fn())).not.toThrow();
  });

  it("does not throw when btn-retry element is absent", () => {
    document.body.innerHTML = `<p id="error-msg"></p>`;
    expect(() => configureErrorPanel("msg", vi.fn())).not.toThrow();
  });
});
