import type { ExtractedPage } from "./types.js";
import type { Highlight } from "../vault/frontmatter.js";

export const MSG_EXTRACT = "EXTRACT" as const;
export const MSG_GET_SELECTION = "GET_SELECTION" as const;

export interface ExtractRequest {
  type: typeof MSG_EXTRACT;
}

export interface GetSelectionRequest {
  type: typeof MSG_GET_SELECTION;
}

export interface GetSelectionResponse {
  text: string;
  offset: number;
}

export interface PushHighlightRequest {
  type: "PUSH_HIGHLIGHT";
  highlight: Highlight;
}

export type ExtractResponse =
  | { ok: true; page: ExtractedPage; selectionOnly: boolean }
  | { ok: false; error: string };
