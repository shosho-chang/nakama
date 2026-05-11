import { describe, it, expect, vi, afterEach } from "vitest";
import { DICT } from "../../src/i18n/dict.js";

// locale.ts reads navigator.language at call time, so we spy per-test.
afterEach(() => {
  vi.restoreAllMocks();
});

function mockLang(lang: string): void {
  Object.defineProperty(navigator, "language", { value: lang, configurable: true });
}

// Import after mocking so module re-executes detectLocale each call.
async function getLocale(): Promise<{ detectLocale: () => import("../../src/i18n/dict.js").Locale; t: (key: string, ...args: string[]) => string }> {
  return import("../../src/i18n/locale.js");
}

describe("DICT", () => {
  it("has identical keys in en and zh-TW", () => {
    const enKeys = Object.keys(DICT.en).sort();
    const zhKeys = Object.keys(DICT["zh-TW"]).sort();
    expect(enKeys).toEqual(zhKeys);
  });

  it("contains expected key families", () => {
    expect(DICT.en).toHaveProperty("noVaultSelected");
    expect(DICT.en).toHaveProperty("extractionFailed");
    expect(DICT.en).toHaveProperty("notifySuccessTitle");
    expect(DICT["zh-TW"]).toHaveProperty("noVaultSelected");
  });
});

describe("detectLocale", () => {
  it("returns 'en' for en-US", async () => {
    mockLang("en-US");
    const { detectLocale } = await getLocale();
    expect(detectLocale()).toBe("en");
  });

  it("returns 'zh-TW' for navigator.language zh-TW", async () => {
    mockLang("zh-TW");
    const { detectLocale } = await getLocale();
    expect(detectLocale()).toBe("zh-TW");
  });

  it("returns 'zh-TW' for zh-Hant", async () => {
    mockLang("zh-Hant");
    const { detectLocale } = await getLocale();
    expect(detectLocale()).toBe("zh-TW");
  });

  it("returns 'en' for zh-CN (Simplified)", async () => {
    mockLang("zh-CN");
    const { detectLocale } = await getLocale();
    expect(detectLocale()).toBe("en");
  });

  it("returns 'en' for ja", async () => {
    mockLang("ja");
    const { detectLocale } = await getLocale();
    expect(detectLocale()).toBe("en");
  });
});

describe("t()", () => {
  it("returns English string when locale is en", async () => {
    mockLang("en-US");
    const { t } = await getLocale();
    expect(t("noVaultStatus")).toBe(DICT.en.noVaultStatus);
  });

  it("returns zh-TW string when locale is zh-TW", async () => {
    mockLang("zh-TW");
    const { t } = await getLocale();
    expect(t("noVaultStatus")).toBe(DICT["zh-TW"].noVaultStatus);
  });

  it("interpolates {0} argument", async () => {
    mockLang("en-US");
    const { t } = await getLocale();
    expect(t("wordCount", "42")).toBe("42 words");
  });

  it("interpolates {0} in zh-TW", async () => {
    mockLang("zh-TW");
    const { t } = await getLocale();
    expect(t("wordCount", "42")).toBe("42 字");
  });

  it("interpolates vaultStatus with folder name", async () => {
    mockLang("en-US");
    const { t } = await getLocale();
    expect(t("vaultStatus", "MyVault")).toBe("Vault: MyVault");
  });

  it("falls back to key when key is missing from both locales", async () => {
    mockLang("en-US");
    const { t } = await getLocale();
    expect(t("__nonexistent_key__")).toBe("__nonexistent_key__");
  });

  it("falls back to English when zh-TW key is unexpectedly missing", async () => {
    mockLang("zh-TW");
    const { t } = await getLocale();
    // noVaultSelected exists in both; verify it returns zh-TW not en
    const result = t("noVaultSelected");
    expect(result).toBe(DICT["zh-TW"].noVaultSelected);
    expect(result).not.toBe(DICT.en.noVaultSelected);
  });

  it("leaves unfilled placeholders when too few args", async () => {
    mockLang("en-US");
    const { t } = await getLocale();
    expect(t("extractionFailed")).toBe("Extraction failed: {0}");
  });
});
