import { getLocale, getTranslations } from "next-intl/server";

import { acceptInvitation } from "@/app/(app)/actions";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiClient, currentTenantId, getMe } from "@/lib/api/client";
import { localized } from "@/lib/localized";

import { CreateTenantForm } from "./create-tenant-form";

// Home: the current business, pending invitations and creating a business.
// Module dashboards (today's appointments, open conversations) arrive with Phase 1.
export default async function Home() {
  const t = await getTranslations();
  const me = await getMe();
  const tenantId = me ? await currentTenantId(me) : null;
  const tenant = me?.tenants.find((x) => x.id === tenantId);
  const locale = await getLocale();
  const { data: catalog } = await (await apiClient()).GET("/api/presets");
  const presets = (catalog ?? []).map((p) => ({
    key: p.key,
    name: localized(p.name_i18n, locale),
    description: localized(p.description_i18n, locale),
  }));

  return (
    <>
      {tenant && (
        <div>
          <h1 className="text-2xl font-semibold tracking-tight" data-testid="business-heading">
            {t("home.greeting", { business: tenant.name })}
          </h1>
          <p className="text-muted-foreground" data-role={tenant.role}>
            {t("home.youAre", { role: t(`common.roles.${tenant.role}`).toLowerCase() })}
          </p>
        </div>
      )}

      {me && me.invitations.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>{t("home.invitations")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {me.invitations.map((inv) => (
              <form
                key={inv.id}
                action={acceptInvitation}
                className="flex flex-wrap items-center justify-between gap-3"
                data-invitation={inv.tenant_name}
              >
                <input type="hidden" name="invitation_id" value={inv.id} />
                <span>
                  {t("home.joinAs", { business: inv.tenant_name, role: t(`common.roles.${inv.role}`).toLowerCase() })}
                </span>
                <Button type="submit" size="sm">
                  {t("common.accept")}
                </Button>
              </form>
            ))}
          </CardContent>
        </Card>
      )}

      {me && (
        <Card>
          <CardHeader>
            <CardTitle>{me.tenants.length === 0 ? t("home.setUp") : t("home.addAnother")}</CardTitle>
          </CardHeader>
          <CardContent>
            <CreateTenantForm presets={presets} />
          </CardContent>
        </Card>
      )}
    </>
  );
}
