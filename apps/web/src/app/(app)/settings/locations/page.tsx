import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { HoursEditor } from "@/components/hours-editor";
import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiClient, inTenant, type Schemas } from "@/lib/api/client";
import { requireTenant } from "@/lib/session";

import { deleteLocation, saveLocation } from "../setup-actions";

export const metadata: Metadata = { title: "Locations · Confluo" };

const TIMEZONES = ["Europe/Brussels", "Europe/Amsterdam", "Europe/Paris", "Europe/Berlin", "Europe/Luxembourg", "Europe/Vienna", "Europe/Zurich", "Europe/Belgrade", "Europe/Tirane", "Europe/London", "Europe/Dublin"];

async function LocationForm({ tenantId, location }: { tenantId: string; location?: Schemas["LocationOut"] }) {
  const t = await getTranslations("setup");
  const tc = await getTranslations("common");
  const prefix = location?.id ?? "new";
  return (
    <form action={saveLocation} className="flex flex-col gap-4" data-location-form={location?.name ?? "new"}>
      <input type="hidden" name="tenant_id" value={tenantId} />
      {location && <input type="hidden" name="id" value={location.id} />}
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="grid gap-1.5">
          <Label htmlFor={`${prefix}-name`}>{t("locations.name")}</Label>
          <Input id={`${prefix}-name`} name="name" defaultValue={location?.name} required />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor={`${prefix}-address`}>{t("locations.address")}</Label>
          <Input id={`${prefix}-address`} name="address" defaultValue={location?.address ?? ""} />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor={`${prefix}-tz`}>{t("locations.timezone")}</Label>
          <select id={`${prefix}-tz`} name="timezone" defaultValue={location?.timezone ?? "Europe/Brussels"} className="h-9 rounded-md border bg-background px-2 text-sm">
            {TIMEZONES.map((tz) => (
              <option key={tz}>{tz}</option>
            ))}
          </select>
        </div>
      </div>
      <p className="text-sm font-medium">{t("locations.hours")}</p>
      <HoursEditor hours={location?.opening_hours ?? {}} prefix={prefix} />
      <Button type="submit" className="self-start">
        {location ? tc("save") : t("add")}
      </Button>
    </form>
  );
}

export default async function LocationsPage({ searchParams }: PageProps<"/settings/locations">) {
  const t = await getTranslations("setup");
  const { tenantId } = await requireTenant();
  const { data: locations } = await (await apiClient()).GET("/api/locations", { params: { header: inTenant(tenantId) } });
  return (
    <>
      <Notice params={await searchParams} />
      {(locations ?? []).map((loc) => (
        <Card key={loc.id}>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>{loc.name}</CardTitle>
            <form action={deleteLocation}>
              <input type="hidden" name="tenant_id" value={tenantId} />
              <input type="hidden" name="id" value={loc.id} />
              <Button type="submit" size="sm" variant="ghost">
                {t("delete")}
              </Button>
            </form>
          </CardHeader>
          <CardContent>
            <LocationForm tenantId={tenantId} location={loc} />
          </CardContent>
        </Card>
      ))}
      <Card>
        <CardHeader>
          <CardTitle>{t("locations.new")}</CardTitle>
        </CardHeader>
        <CardContent>
          <LocationForm tenantId={tenantId} />
        </CardContent>
      </Card>
    </>
  );
}
