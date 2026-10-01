"use client";

import { useRef } from "react";

import { switchTenant } from "@/app/(app)/actions";

type Option = { id: string; name: string };

// Native select that submits on change; works without JS as a plain form too.
export function TenantSwitcher({ tenants, current }: { tenants: Option[]; current: string }) {
  const form = useRef<HTMLFormElement>(null);
  return (
    <form ref={form} action={switchTenant}>
      <label className="sr-only" htmlFor="tenant_id">
        Business
      </label>
      <select
        id="tenant_id"
        name="tenant_id"
        defaultValue={current}
        key={current}
        onChange={() => form.current?.requestSubmit()}
        className="h-8 rounded-md border bg-background px-2 text-sm font-medium"
      >
        {tenants.map((t) => (
          <option key={t.id} value={t.id}>
            {t.name}
          </option>
        ))}
      </select>
      <noscript>
        <button type="submit">Switch</button>
      </noscript>
    </form>
  );
}
