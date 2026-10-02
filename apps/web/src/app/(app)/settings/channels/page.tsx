import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiClient, inTenant } from "@/lib/api/client";
import { requireModule } from "@/lib/session";

import { saveWhatsApp } from "../setup-actions";

export const metadata: Metadata = { title: "Channels · Confluo" };

export default async function ChannelsPage({ searchParams }: PageProps<"/settings/channels">) {
  const t = await getTranslations("settings.channels");
  const { tenantId } = await requireModule("crm");
  const { data: wa } = await (await apiClient()).GET("/api/crm/channels/whatsapp", { params: { header: inTenant(tenantId) } });

  return (
    <>
      <Notice params={await searchParams} />
      <Card data-testid="whatsapp-card">
        <CardHeader>
          <CardTitle>WhatsApp</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4 text-sm">
          <p className="text-muted-foreground">{t("whatsappIntro")}</p>
          {wa && !wa.webhook_ready && <p className="rounded-md bg-amber-50 p-2 text-amber-900 dark:bg-amber-950 dark:text-amber-100">{t("notReady")}</p>}
          {wa?.phone_number_id && (
            <p data-whatsapp-status={wa.enabled ? "on" : "off"}>
              {wa.enabled ? t("connected", { phone: wa.display_phone || wa.phone_number_id }) : t("paused")}
            </p>
          )}
          <form action={saveWhatsApp} className="grid gap-3 sm:grid-cols-2">
            <input type="hidden" name="tenant_id" value={tenantId} />
            <div className="grid gap-1.5">
              <Label htmlFor="wa-number-id">{t("phoneNumberId")}</Label>
              <Input id="wa-number-id" name="phone_number_id" required inputMode="numeric" defaultValue={wa?.phone_number_id ?? ""} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="wa-display">{t("displayPhone")}</Label>
              <Input id="wa-display" name="display_phone" placeholder="+1 555 …" defaultValue={wa?.display_phone ?? ""} />
            </div>
            <div className="grid gap-1.5 sm:col-span-2">
              <Label htmlFor="wa-token">{t("accessToken")}</Label>
              <Input id="wa-token" name="access_token" type="password" autoComplete="off" placeholder={wa?.has_token ? t("tokenKept") : "EAA…"} required={!wa?.has_token} />
            </div>
            <label className="flex items-center gap-2 sm:col-span-2">
              <input type="checkbox" name="enabled" defaultChecked={wa?.enabled ?? true} />
              {t("enabled")}
            </label>
            <Button type="submit" className="self-start sm:col-span-2 sm:justify-self-start" data-testid="save-whatsapp">
              {t("save")}
            </Button>
          </form>
        </CardContent>
      </Card>
    </>
  );
}
