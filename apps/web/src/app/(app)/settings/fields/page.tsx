import type { Metadata } from "next";
import { getLocale, getTranslations } from "next-intl/server";

import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiClient, inTenant } from "@/lib/api/client";
import { localized } from "@/lib/localized";
import { requireTenant } from "@/lib/session";

import { archiveField, createField } from "../setup-actions";

export const metadata: Metadata = { title: "Booking fields · Confluo" };

const TYPES = ["text", "long_text", "number", "date", "boolean", "select", "phone", "email"] as const;

export default async function FieldsPage({ searchParams }: PageProps<"/settings/fields">) {
  const t = await getTranslations("setup");
  const locale = await getLocale();
  const { tenantId } = await requireTenant();
  const { data: fields } = await (await apiClient()).GET("/api/fields", { params: { header: inTenant(tenantId) } });

  return (
    <>
      <Notice params={await searchParams} />
      <Card>
        <CardContent>
          {(fields ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("none")}</p>
          ) : (
            <ul className="divide-y text-sm">
              {(fields ?? []).map((f) => (
                <li key={f.id} className="flex flex-wrap items-center gap-3 py-2" data-field={f.key}>
                  <span className="min-w-0 flex-1 font-medium">{localized(f.label_i18n, locale)}</span>
                  <span className="text-muted-foreground">
                    {t(`fields.types.${f.type}`)} · {t(`fields.entity.${f.entity}`)} · {t(`fields.pii.${f.pii_level}`)}
                    {f.options.length > 0 && ` · ${f.options.join(", ")}`}
                  </span>
                  <form action={archiveField}>
                    <input type="hidden" name="tenant_id" value={tenantId} />
                    <input type="hidden" name="id" value={f.id} />
                    <Button type="submit" size="sm" variant="ghost">
                      {t("fields.archive")}
                    </Button>
                  </form>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>{t("fields.new")}</CardTitle>
        </CardHeader>
        <CardContent>
          <form action={createField} className="grid gap-3 sm:grid-cols-2">
            <input type="hidden" name="tenant_id" value={tenantId} />
            <input type="hidden" name="lang" value={locale} />
            <div className="grid gap-1.5">
              <Label htmlFor="field-label">{t("fields.label")}</Label>
              <Input id="field-label" name="label" required maxLength={120} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="field-type">{t("fields.type")}</Label>
              <select id="field-type" name="type" className="h-9 rounded-md border bg-background px-2 text-sm">
                {TYPES.map((type) => (
                  <option key={type} value={type}>
                    {t(`fields.types.${type}`)}
                  </option>
                ))}
              </select>
            </div>
            <div className="grid gap-1.5 sm:col-span-2">
              <Label htmlFor="field-options">{t("fields.options")}</Label>
              <Input id="field-options" name="options" />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="field-entity">{t("fields.appliesTo")}</Label>
              <select id="field-entity" name="entity" className="h-9 rounded-md border bg-background px-2 text-sm">
                <option value="appointment">{t("fields.entity.appointment")}</option>
                <option value="customer">{t("fields.entity.customer")}</option>
              </select>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="field-pii">{t("fields.sensitivity")}</Label>
              <select id="field-pii" name="pii_level" defaultValue="personal" className="h-9 rounded-md border bg-background px-2 text-sm">
                {(["none", "personal", "sensitive"] as const).map((p) => (
                  <option key={p} value={p}>
                    {t(`fields.pii.${p}`)}
                  </option>
                ))}
              </select>
            </div>
            <Button type="submit" className="self-start sm:col-span-2 sm:justify-self-start" data-testid="add-field">
              {t("add")}
            </Button>
          </form>
        </CardContent>
      </Card>
    </>
  );
}
