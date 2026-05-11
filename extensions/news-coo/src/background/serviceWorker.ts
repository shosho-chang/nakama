import { quickClip } from "./quickClip.js";
import { pushHighlight } from "../shared/highlights.js";
import { MSG_GET_SELECTION } from "../shared/messages.js";
import type { GetSelectionResponse } from "../shared/messages.js";

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: "clip-page",
      title: "News Coo: Clip page",
      contexts: ["page"],
    });
    chrome.contextMenus.create({
      id: "clip-selection",
      title: "News Coo: Clip selection",
      contexts: ["selection"],
    });
  });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (!tab?.id) return;
  if (info.menuItemId === "clip-page" || info.menuItemId === "clip-selection") {
    void quickClip(tab.id);
  }
});

chrome.commands.onCommand.addListener((command) => {
  if (command === "quick_clip") {
    void chrome.tabs.query({ active: true, currentWindow: true }).then((tabs) => {
      const tab = tabs[0];
      if (tab?.id !== undefined) void quickClip(tab.id);
    });
  } else if (command === "mark_highlight") {
    void chrome.tabs.query({ active: true, currentWindow: true }).then(async (tabs) => {
      const tab = tabs[0];
      if (!tab?.id) return;
      const tabId = tab.id;
      const resp: GetSelectionResponse | null = await chrome.tabs.sendMessage(tabId, {
        type: MSG_GET_SELECTION,
      });
      if (!resp?.text) return;
      await pushHighlight(tabId, { text: resp.text, offset: resp.offset });
    });
  }
});
