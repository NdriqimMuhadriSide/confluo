"use client";

import { useTranslations } from "next-intl";
import { useRef } from "react";

import { switchTenant } from "@/app/(app)/actions";

type Option = { id: string; name: string };

// Native select that submits on change.
export function TenantSwitcher({
  tenants,
  current,
  id = "tenant_id",
}: {
  tenants: Option[];
  current: string;
  id?: string;
}) {
  const t = useTranslations("common");
  const form = useRef<HTMLFormElement>(null);
  return (
    <form ref={form} action={switchTenant} className="min-w-0">
      <label className="sr-only" htmlFor={id}>
        {t("business")}
      </label>
      <select
        id={id}
        name="tenant_id"
        defaultValue={current}
        key={current}
        onChange={() => form.current?.requestSubmit()}
        className="h-9 w-full max-w-full truncate rounded-md border bg-background px-2 text-sm font-medium"
        data-testid="tenant-switcher"
      >
        {tenants.map((t) => (
          <option key={t.id} value={t.id}>
            {t.name}
          </option>
        ))}
      </select>
    </form>
  );
}
