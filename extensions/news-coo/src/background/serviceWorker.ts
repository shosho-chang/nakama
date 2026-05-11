import { quickClip } from "./quickClip.js";

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
  if (command !== "quick_clip") return;
  void chrome.tabs.query({ active: true, currentWindow: true }).then((tabs) => {
    const tab = tabs[0];
    if (tab?.id !== undefined) void quickClip(tab.id);
  });
});
