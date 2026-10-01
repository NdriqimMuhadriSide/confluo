import type { Metadata } from "next";
import { getFormatter, getLocale, getTranslations } from "next-intl/server";

import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiClient, inTenant, type Schemas } from "@/lib/api/client";
import { localized } from "@/lib/localized";
import { requireTenant } from "@/lib/session";

import { deleteService, saveService } from "../setup-actions";

export const metadata: Metadata = { title: "Services · Confluo" };

const LANGS = ["en", "nl", "fr", "de", "sq"] as const;

type Props = {
  tenantId: string;
  service?: Schemas["ServiceOut"];
  fields: Schemas["FieldOut"][];
  resources: Schemas["ResourceOut"][];
};

async function ServiceForm({ tenantId, service, fields, resources }: Props) {
  const t = await getTranslations("setup");
  const tc = await getTranslations("common");
  const locale = await getLocale();
  const p = service?.id ?? "new";
  const chosen = new Map((service?.fields ?? []).map((f) => [f.field_id, f.required]));
  const others = LANGS.filter((l) => l !== locale);
  return (
    <form action={saveService} className="flex flex-col gap-4" data-service-form={service ? localized(service.name_i18n, locale) : "new"}>
      <input type="hidden" name="tenant_id" value={tenantId} />
      {service && <input type="hidden" name="id" value={service.id} />}
      <div className="grid gap-1.5">
        <Label htmlFor={`${p}-name`}>{t("services.name")}</Label>
        <Input id={`${p}-name`} name={`name_${locale}`} defaultValue={service?.name_i18n[locale] ?? ""} required />
      </div>
      <details className="text-sm">
        <summary className="cursor-pointer text-muted-foreground">{t("services.otherLanguages")}</summary>
        <div className="mt-2 grid gap-2 sm:grid-cols-2">
          {others.map((l) => (
            <div key={l} className="grid gap-1">
              <Label htmlFor={`${p}-name-${l}`}>{l.toUpperCase()}</Label>
              <Input id={`${p}-name-${l}`} name={`name_${l}`} defaultValue={service?.name_i18n[l] ?? ""} />
            </div>
          ))}
        </div>
      </details>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {(
          [
            ["duration_min", t("services.duration"), service?.duration_min ?? 30, 5],
            ["buffer_before_min", t("services.bufferBefore"), service?.buffer_before_min ?? 0, 0],
            ["buffer_after_min", t("services.bufferAfter"), service?.buffer_after_min ?? 0, 0],
          ] as const
        ).map(([name, label, value, min]) => (
          <div key={name} className="grid gap-1.5">
            <Label htmlFor={`${p}-${name}`}>{label}</Label>
            <Input id={`${p}-${name}`} name={name} type="number" min={min} max={name === "duration_min" ? 1440 : 240} defaultValue={value} required />
          </div>
        ))}
        <div className="grid gap-1.5">
          <Label htmlFor={`${p}-price`}>{t("services.price")}</Label>
          <Input id={`${p}-price`} name="price" type="number" min={0} step="0.01" defaultValue={service?.price_cents != null ? service.price_cents / 100 : ""} />
        </div>
      </div>
      {fields.length > 0 && (
        <fieldset className="grid gap-1 text-sm">
          <legend className="mb-1 font-medium">{t("services.requiredFields")}</legend>
          {fields.map((f) => (
            <div key={f.id} className="flex flex-wrap items-center gap-3">
              <label className="flex items-center gap-2">
                <input type="checkbox" name="field" value={f.id} defaultChecked={chosen.has(f.id)} />
                {localized(f.label_i18n, locale)}
              </label>
              <label className="flex items-center gap-1 text-muted-foreground">
                <input type="checkbox" name={`required_${f.id}`} defaultChecked={chosen.get(f.id) ?? true} />
                {t("services.required")}
              </label>
            </div>
          ))}
        </fieldset>
      )}
      {resources.length > 0 && (
        <fieldset className="grid gap-1 text-sm">
          <legend className="mb-1 font-medium">{t("services.performedBy")}</legend>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {resources.map((r) => (
              <label key={r.id} className="flex items-center gap-2">
                <input type="checkbox" name="resource" value={r.id} defaultChecked={service?.resource_ids.includes(r.id) ?? true} />
                {r.name}
              </label>
            ))}
          </div>
        </fieldset>
      )}
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" name="active" defaultChecked={service?.active ?? true} />
        {t("services.active")}
      </label>
      <Button type="submit" className="self-start" data-testid={service ? "save-service" : "add-service"}>
        {service ? tc("save") : t("add")}
      </Button>
    </form>
  );
}

export default async function ServicesPage({ searchParams }: PageProps<"/settings/services">) {
  const t = await getTranslations("setup");
  const format = await getFormatter();
  const locale = await getLocale();
  const { tenantId } = await requireTenant();
  const api = await apiClient();
  const params = { header: inTenant(tenantId) };
  const [{ data: services }, { data: fields }, { data: resources }] = await Promise.all([
    api.GET("/api/crm/services", { params }),
    api.GET("/api/fields", { params }),
    api.GET("/api/crm/resources", { params }),
  ]);

  return (
    <>
      <Notice params={await searchParams} />
      {(services ?? []).map((s) => (
        <Card key={s.id} data-service={localized(s.name_i18n, locale)}>
          <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
            <CardTitle>
              {localized(s.name_i18n, locale)}{" "}
              <span className="text-sm font-normal text-muted-foreground">
                {t("services.minutes", { count: s.duration_min })}
                {s.price_cents != null && ` · ${format.number(s.price_cents / 100, { style: "currency", currency: "EUR" })}`}
                {!s.active && ` · ${t("services.inactive")}`}
              </span>
            </CardTitle>
            <form action={deleteService}>
              <input type="hidden" name="tenant_id" value={tenantId} />
              <input type="hidden" name="id" value={s.id} />
              <Button type="submit" size="sm" variant="ghost">
                {t("delete")}
              </Button>
            </form>
          </CardHeader>
          <CardContent>
            <details>
              <summary className="cursor-pointer text-sm text-muted-foreground">{t("edit")}</summary>
              <div className="mt-3">
                <ServiceForm tenantId={tenantId} service={s} fields={fields ?? []} resources={resources ?? []} />
              </div>
            </details>
          </CardContent>
        </Card>
      ))}
      <Card>
        <CardHeader>
          <CardTitle>{t("services.new")}</CardTitle>
        </CardHeader>
        <CardContent>
          <ServiceForm tenantId={tenantId} fields={fields ?? []} resources={resources ?? []} />
        </CardContent>
      </Card>
    </>
  );
}
