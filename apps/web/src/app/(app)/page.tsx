import { acceptInvitation } from "@/app/(app)/actions";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { API_URL, currentTenantId, getHealth, getMe } from "@/lib/api/client";

import { CreateTenantForm } from "./create-tenant-form";

// Placeholder home: invitations, first-business setup, and a stack status card.
// The real dashboard shell comes with its own card.
export default async function Home() {
  const [health, me] = await Promise.all([getHealth(), getMe()]);
  const tenantId = me ? await currentTenantId(me) : null;
  const tenant = me?.tenants.find((t) => t.id === tenantId);

  const rows: [string, boolean][] = [
    ["API", health !== null],
    ["Database", health?.database ?? false],
    ["API accepts your session", me !== null],
  ];

  return (
    <>
      {tenant && (
        <h1 className="text-2xl font-semibold tracking-tight">
          {tenant.name} <span className="text-base font-normal text-muted-foreground">· {tenant.role}</span>
        </h1>
      )}

      {me && me.invitations.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Invitations</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {me.invitations.map((inv) => (
              <form key={inv.id} action={acceptInvitation} className="flex items-center justify-between gap-4">
                <input type="hidden" name="invitation_id" value={inv.id} />
                <span>
                  Join <strong>{inv.tenant_name}</strong> as {inv.role}
                </span>
                <Button type="submit" size="sm">
                  Accept
                </Button>
              </form>
            ))}
          </CardContent>
        </Card>
      )}

      {me && (
        <Card>
          <CardHeader>
            <CardTitle>{me.tenants.length === 0 ? "Set up your business" : "Add another business"}</CardTitle>
          </CardHeader>
          <CardContent>
            <CreateTenantForm />
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Local stack</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {rows.map(([name, ok]) => (
            <div key={name} className="flex items-center justify-between">
              <span>{name}</span>
              <span className={ok ? "text-emerald-600" : "text-red-600"}>{ok ? "up" : "down"}</span>
            </div>
          ))}
          <p className="pt-2 text-xs text-muted-foreground">{API_URL}</p>
        </CardContent>
      </Card>
    </>
  );
}
