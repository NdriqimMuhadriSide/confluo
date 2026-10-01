// Pick the text for the current language from a {lang: text} map, falling back to
// English and then to whatever exists.
export function localized(texts: Record<string, string>, locale: string): string {
  return texts[locale] ?? texts.en ?? Object.values(texts)[0] ?? "";
}
