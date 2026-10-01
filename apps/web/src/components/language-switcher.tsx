"use client";

import { useLocale, useTranslations } from "next-intl";
import { useRef } from "react";

import { LOCALE_NAMES, LOCALES } from "@/i18n/config";
import { setLocale } from "@/i18n/actions";

export function LanguageSwitcher({ id = "locale" }: { id?: string }) {
  const t = useTranslations("common");
  const locale = useLocale();
  const form = useRef<HTMLFormElement>(null);
  return (
    <form ref={form} action={setLocale}>
      <label className="sr-only" htmlFor={id}>
        {t("language")}
      </label>
      <select
        id={id}
        name="locale"
        defaultValue={locale}
        key={locale}
        onChange={() => form.current?.requestSubmit()}
        className="h-8 rounded-md border bg-background px-2 text-sm"
        data-testid="language-switcher"
      >
        {LOCALES.map((l) => (
          <option key={l} value={l}>
            {LOCALE_NAMES[l]}
          </option>
        ))}
      </select>
    </form>
  );
}
