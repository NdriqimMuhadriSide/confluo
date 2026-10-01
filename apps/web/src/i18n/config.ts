// Dashboard languages (ARCHITECTURE.md §5, decision 7). The choice lives in a cookie;
// without one we negotiate from the browser's Accept-Language.
export const LOCALES = ["en", "nl", "fr", "de", "sq"] as const;
export type Locale = (typeof LOCALES)[number];
export const DEFAULT_LOCALE: Locale = "en";
export const LOCALE_COOKIE = "NEXT_LOCALE";

// Each language named in itself, for the switcher.
export const LOCALE_NAMES: Record<Locale, string> = {
  en: "English",
  nl: "Nederlands",
  fr: "Français",
  de: "Deutsch",
  sq: "Shqip",
};

export function isLocale(value: string | undefined | null): value is Locale {
  return !!value && (LOCALES as readonly string[]).includes(value);
}

export function negotiate(acceptLanguage: string | null): Locale {
  for (const part of (acceptLanguage ?? "").split(",")) {
    const tag = part.split(";")[0].trim().toLowerCase().split("-")[0];
    if (isLocale(tag)) return tag;
  }
  return DEFAULT_LOCALE;
}
