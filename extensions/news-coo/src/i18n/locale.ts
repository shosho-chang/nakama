import { DICT, type Locale } from "./dict.js";

export function detectLocale(): Locale {
  if (typeof navigator !== "undefined") {
    const lang = navigator.language;
    if (lang === "zh-TW" || lang === "zh-Hant" || lang.startsWith("zh-Hant-")) {
      return "zh-TW";
    }
  }
  return "en";
}

export function t(key: string, ...args: string[]): string {
  const locale = detectLocale();
  const str = DICT[locale]?.[key] ?? DICT.en[key] ?? key;
  return str.replace(/\{(\d+)\}/g, (_, i: string) => args[Number(i)] ?? `{${i}}`);
}
