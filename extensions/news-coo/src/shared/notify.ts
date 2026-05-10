export function notifySuccess(slug: string): void {
  chrome.notifications.create(`news-coo-ok-${Date.now()}`, {
    type: "basic",
    iconUrl: "icons/icon-48.png",
    title: "News Coo — Saved",
    message: `Inbox/kb/${slug}.md`,
  });
}

export function notifyError(message: string): void {
  chrome.notifications.create(`news-coo-err-${Date.now()}`, {
    type: "basic",
    iconUrl: "icons/icon-48.png",
    title: "News Coo — Error",
    message,
  });
}
