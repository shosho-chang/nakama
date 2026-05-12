import { loadHandle, verifyHandle } from "../vault/handle.js";
import { writePageToVault } from "../vault/writer.js";
import { notifySuccess, notifyError } from "../shared/notify.js";
import { getHighlights, clearHighlights } from "../shared/highlights.js";
import { MSG_EXTRACT } from "../shared/messages.js";
import type { ExtractResponse } from "../shared/messages.js";
import type { Highlight } from "../vault/frontmatter.js";
import { t } from "../i18n/locale.js";

export interface QuickClipDeps {
  loadHandle: () => Promise<FileSystemDirectoryHandle | null>;
  verifyHandle: (h: FileSystemDirectoryHandle) => Promise<boolean>;
  sendExtract: (tabId: number) => Promise<ExtractResponse>;
  getHighlights: (tabId: number) => Promise<Highlight[]>;
  clearHighlights: (tabId: number) => Promise<void>;
  notifySuccess: (slug: string) => void;
  notifyError: (msg: string) => void;
  writePageToVault: typeof writePageToVault;
}

function defaultSendExtract(tabId: number): Promise<ExtractResponse> {
  return chrome.tabs.sendMessage(tabId, { type: MSG_EXTRACT });
}

const defaultDeps: QuickClipDeps = {
  loadHandle,
  verifyHandle,
  sendExtract: defaultSendExtract,
  getHighlights,
  clearHighlights,
  notifySuccess,
  notifyError,
  writePageToVault,
};

export async function quickClip(
  tabId: number,
  deps: QuickClipDeps = defaultDeps,
): Promise<void> {
  const handle = await deps.loadHandle();
  if (!handle) {
    deps.notifyError(t("noVaultSelectedShort"));
    return;
  }

  const ok = await deps.verifyHandle(handle);
  if (!ok) {
    deps.notifyError(t("vaultPermissionRevoked"));
    return;
  }

  let response: ExtractResponse;
  try {
    response = await deps.sendExtract(tabId);
  } catch {
    deps.notifyError(t("pageUnreachable"));
    return;
  }

  if (!response.ok) {
    deps.notifyError(t("extractionFailed", response.error));
    return;
  }

  const highlights = await deps.getHighlights(tabId);

  try {
    const result = await deps.writePageToVault(handle, response.page, {
      highlights,
      frontmatterOpts: {
        selectionOnly: response.selectionOnly,
        extractionMethod: response.selectionOnly ? "selection" : "defuddle",
      },
    });
    await deps.clearHighlights(tabId);
    deps.notifySuccess(result.slug);
  } catch (err) {
    deps.notifyError(t("writeFailed", String(err)));
  }
}
