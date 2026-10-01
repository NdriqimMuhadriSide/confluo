import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { getTranslations } from "next-intl/server";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiClient, inTenant } from "@/lib/api/client";
import { requireTenant } from "@/lib/session";

import { saveConfig, toggleModule } from "./actions";

export const metadata: Metadata = { title: "Modules · Confluo" };

type Prop = {
  type?: string;
  title?: string;
  description?: string;
  enum?: string[];
  minimum?: number;
  maximum?: number;
};

// Renders a module's settings from its JSON schema: enums as selects, booleans as
// checkboxes, numbers and strings as inputs. Enough for flat module configs.
function Field({ name, prop, value }: { name: string; prop: Prop; value: unknown }) {
  const id = `cfg-${name}`;
  const label = prop.title ?? name;
  if (prop.type === "boolean") {
    return (
      <label className="flex items-start gap-2 text-sm">
        <input id={id} name={name} type="checkbox" defaultChecked={Boolean(value)} className="mt-1" />
        <span>
          <span className="font-medium">{label}</span>
          {prop.description && <span className="block text-muted-foreground">{prop.description}</span>}
        </span>
      </label>
    );
  }
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {prop.enum ? (
        <select id={id} name={name} defaultValue={String(value)} className="h-9 rounded-md border bg-background px-2 text-sm">
          {prop.enum.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
      ) : (
        <Input
          id={id}
          name={name}
          type={prop.type === "integer" || prop.type === "number" ? "number" : "text"}
          min={prop.minimum}
          max={prop.maximum}
          defaultValue={value === null || value === undefined ? "" : String(value)}
        />
      )}
      {prop.description && <p className="text-xs text-muted-foreground">{prop.description}</p>}
    </div>
  );
}

export default async function ModulesPage({ searchParams }: PageProps<"/settings/modules">) {
  const t = await getTranslations("settings.modules");
  const { tenantId } = await requireTenant();
  const { data: modules, response } = await (await apiClient()).GET("/api/modules", {
    params: { header: inTenant(tenantId) },
  });
  if (response.status === 403) redirect("/");
  const { error, saved } = await searchParams;

  return (
    <>
      {typeof error === "string" && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {saved && !error && (
        <p role="status" className="text-sm text-muted-foreground">
          {t("saved")}
        </p>
      )}
      {(modules ?? []).map((m) => {
        const props = (m.config_schema.properties ?? {}) as Record<string, Prop>;
        const types = Object.fromEntries(Object.entries(props).map(([k, p]) => [k, p.type ?? "string"]));
        return (
          <Card key={m.key} data-module={m.key}>
            <CardHeader className="flex flex-row items-center justify-between gap-4">
              <CardTitle>
                {m.key.toUpperCase()} <span className="text-sm font-normal text-muted-foreground">v{m.version}</span>
              </CardTitle>
              <form action={toggleModule}>
                <input type="hidden" name="tenant_id" value={tenantId} />
                <input type="hidden" name="key" value={m.key} />
                <input type="hidden" name="enabled" value={String(!m.enabled)} />
                <Button type="submit" size="sm" variant={m.enabled ? "outline" : "default"}>
                  {m.enabled ? t("turnOff") : t("turnOn")}
                </Button>
              </form>
            </CardHeader>
            {m.enabled && Object.keys(props).length > 0 && (
              <CardContent>
                <form action={saveConfig} className="flex flex-col gap-4">
                  <input type="hidden" name="tenant_id" value={tenantId} />
                  <input type="hidden" name="key" value={m.key} />
                  <input type="hidden" name="__types" value={JSON.stringify(types)} />
                  {Object.entries(props).map(([name, prop]) => (
                    <Field key={name} name={name} prop={prop} value={m.config[name]} />
                  ))}
                  <Button type="submit" className="self-start">
                    {t("saveSettings")}
                  </Button>
                </form>
              </CardContent>
            )}
          </Card>
        );
      })}
    </>
  );
}
