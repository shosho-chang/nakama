import type { ExtractedPage } from "./types.js";

export const MSG_EXTRACT = "EXTRACT" as const;

export interface ExtractRequest {
  type: typeof MSG_EXTRACT;
}

export type ExtractResponse =
  | { ok: true; page: ExtractedPage }
  | { ok: false; error: string };
