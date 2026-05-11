import type { Highlight } from "../vault/frontmatter.js";

const key = (tabId: number) => `highlights-${tabId}`;

export async function getHighlights(tabId: number): Promise<Highlight[]> {
  const k = key(tabId);
  const result = await chrome.storage.session.get(k);
  return (result[k] as Highlight[] | undefined) ?? [];
}

export async function pushHighlight(tabId: number, h: Highlight): Promise<void> {
  const existing = await getHighlights(tabId);
  await chrome.storage.session.set({ [key(tabId)]: [...existing, h] });
}

export async function clearHighlights(tabId: number): Promise<void> {
  await chrome.storage.session.remove(key(tabId));
}
