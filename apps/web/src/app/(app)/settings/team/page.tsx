import type { Metadata } from "next";
import Link from "next/link";
import { getTranslations } from "next-intl/server";

import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiClient, inTenant } from "@/lib/api/client";
import { requireTenant } from "@/lib/session";

import { createResource } from "../setup-actions";

export const metadata: Metadata = { title: "Staff & rooms · Confluo" };

export default async function TeamPage({ searchParams }: PageProps<"/settings/team">) {
  const t = await getTranslations("setup");
  const { tenantId } = await requireTenant();
  const api = await apiClient();
  const params = { header: inTenant(tenantId) };
  const [{ data: resources }, { data: locations }, { data: members }] = await Promise.all([
    api.GET("/api/crm/resources", { params }),
    api.GET("/api/locations", { params }),
    api.GET("/api/members", { params }),
  ]);

  return (
    <>
      <Notice params={await searchParams} />
      <Card>
        <CardContent>
          {(resources ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("none")}</p>
          ) : (
            <ul className="divide-y text-sm">
              {(resources ?? []).map((r) => (
                <li key={r.id} className="flex flex-wrap items-center gap-3 py-2" data-resource={r.name}>
                  <span className="min-w-0 flex-1 font-medium">{r.name}</span>
                  <span className="text-muted-foreground">{t(`team.kinds.${r.kind}`)}</span>
                  <Link href={`/settings/team/${r.id}`} className="underline underline-offset-4">
                    {t("team.manage")}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>{t("team.new")}</CardTitle>
        </CardHeader>
        <CardContent>
          <form action={createResource} className="grid gap-3 sm:grid-cols-2">
            <input type="hidden" name="tenant_id" value={tenantId} />
            <div className="grid gap-1.5">
              <Label htmlFor="res-name">{t("team.name")}</Label>
              <Input id="res-name" name="name" required />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="res-kind">{t("team.kind")}</Label>
              <select id="res-kind" name="kind" className="h-9 rounded-md border bg-background px-2 text-sm">
                {(["staff", "room", "equipment"] as const).map((k) => (
                  <option key={k} value={k}>
                    {t(`team.kinds.${k}`)}
                  </option>
                ))}
              </select>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="res-location">{t("team.location")}</Label>
              <select id="res-location" name="location_id" className="h-9 rounded-md border bg-background px-2 text-sm">
                <option value="">{t("team.noLocation")}</option>
                {(locations ?? []).map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="res-member">{t("team.member")}</Label>
              <select id="res-member" name="member_id" className="h-9 rounded-md border bg-background px-2 text-sm">
                <option value="">{t("team.noMember")}</option>
                {(members ?? []).map((m) => (
                  <option key={m.user_id} value={m.user_id}>
                    {m.name || m.email}
                  </option>
                ))}
              </select>
            </div>
            <Button type="submit" className="self-start sm:col-span-2 sm:justify-self-start" data-testid="add-resource">
              {t("add")}
            </Button>
          </form>
        </CardContent>
      </Card>
    </>
  );
}
