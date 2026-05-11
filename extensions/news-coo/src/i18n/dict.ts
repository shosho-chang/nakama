export type Locale = "en" | "zh-TW";

export const DICT: Record<Locale, Record<string, string>> = {
  en: {
    // vault / options
    noVaultSelected: "No vault selected. Open extension options to pick a folder.",
    noVaultSelectedShort: "No vault selected. Open options to pick a folder.",
    vaultPermissionRevoked: "Vault permission revoked. Open extension options to re-pick.",
    vaultStatus: "Vault: {0}",
    noVaultStatus: "No vault selected.",

    // extraction / tab errors
    noActiveTab: "Could not determine active tab.",
    pageUnreachable: "Could not reach page. Try reloading the tab.",
    extractionFailed: "Extraction failed: {0}",
    writeFailed: "Write failed: {0}",

    // popup metadata labels
    wordCount: "{0} words",
    imageCount: "{0} image",
    imageCountPlural: "{0} images",
    highlightCount: "{0} highlight",
    highlightCountPlural: "{0} highlights",

    // notifications
    notifySuccessTitle: "News Coo — Saved",
    notifyErrorTitle: "News Coo — Error",
  },

  "zh-TW": {
    // vault / options
    noVaultSelected: "尚未選擇 Vault 資料夾。請開啟擴充功能選項進行設定。",
    noVaultSelectedShort: "尚未選擇 Vault 資料夾。請開啟選項進行設定。",
    vaultPermissionRevoked: "Vault 權限已撤銷。請開啟擴充功能選項重新選擇資料夾。",
    vaultStatus: "Vault：{0}",
    noVaultStatus: "尚未選擇 Vault 資料夾。",

    // extraction / tab errors
    noActiveTab: "無法取得目前分頁。",
    pageUnreachable: "無法連線至頁面，請重新載入分頁後再試。",
    extractionFailed: "擷取失敗：{0}",
    writeFailed: "寫入失敗：{0}",

    // popup metadata labels
    wordCount: "{0} 字",
    imageCount: "{0} 張圖片",
    imageCountPlural: "{0} 張圖片",
    highlightCount: "{0} 個標記",
    highlightCountPlural: "{0} 個標記",

    // notifications
    notifySuccessTitle: "News Coo — 已儲存",
    notifyErrorTitle: "News Coo — 錯誤",
  },
} as const;
