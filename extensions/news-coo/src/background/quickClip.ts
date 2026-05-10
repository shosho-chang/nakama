import { loadHandle, verifyHandle } from "../vault/handle.js";
import { writePageToVault } from "../vault/writer.js";
import { notifySuccess, notifyError } from "../shared/notify.js";
import { MSG_EXTRACT } from "../shared/messages.js";
import type { ExtractResponse } from "../shared/messages.js";

export interface QuickClipDeps {
  loadHandle: () => Promise<FileSystemDirectoryHandle | null>;
  verifyHandle: (h: FileSystemDirectoryHandle) => Promise<boolean>;
  sendExtract: (tabId: number) => Promise<ExtractResponse>;
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
    deps.notifyError("No vault selected. Open options to pick a folder.");
    return;
  }

  const ok = await deps.verifyHandle(handle);
  if (!ok) {
    deps.notifyError("Vault permission revoked. Open extension options to re-pick.");
    return;
  }

  let response: ExtractResponse;
  try {
    response = await deps.sendExtract(tabId);
  } catch {
    deps.notifyError("Could not reach page. Try reloading the tab.");
    return;
  }

  if (!response.ok) {
    deps.notifyError(`Extraction failed: ${response.error}`);
    return;
  }

  try {
    const result = await deps.writePageToVault(handle, response.page, {
      fetchImages: false,
    });
    deps.notifySuccess(result.slug);
  } catch (err) {
    deps.notifyError(`Write failed: ${String(err)}`);
  }
}
