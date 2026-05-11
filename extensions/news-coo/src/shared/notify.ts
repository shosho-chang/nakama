import { t } from "../i18n/locale.js";

export function notifySuccess(slug: string): void {
  chrome.notifications.create(`news-coo-ok-${Date.now()}`, {
    type: "basic",
    iconUrl: "icons/icon-48.png",
    title: t("notifySuccessTitle"),
    message: `Inbox/kb/${slug}.md`,
  });
}

export function notifyError(message: string): void {
  chrome.notifications.create(`news-coo-err-${Date.now()}`, {
    type: "basic",
    iconUrl: "icons/icon-48.png",
    title: t("notifyErrorTitle"),
    message,
  });
}
