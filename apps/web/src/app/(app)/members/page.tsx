import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { changeRole, removeMember, revokeInvitation } from "@/app/(app)/actions";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiClient, currentTenantId, getMe, inTenant } from "@/lib/api/client";

import { InviteForm } from "./invite-form";

export const metadata: Metadata = { title: "Members · Confluo" };

const ROLES = ["owner", "admin", "staff"] as const;

export default async function MembersPage({ searchParams }: PageProps<"/members">) {
  const me = await getMe();
  const tenantId = me ? await currentTenantId(me) : null;
  if (!me || !tenantId) redirect("/");

  const api = await apiClient();
  const params = { header: inTenant(tenantId) };
  const [{ data: tenant }, { data: members }] = await Promise.all([
    api.GET("/api/tenant", { params }),
    api.GET("/api/members", { params }),
  ]);
  if (!tenant || !members) redirect("/");

  const canManage = tenant.permissions.includes("core.members.manage");
  const isOwner = tenant.role === "owner";
  const { data: invitations } = canManage
    ? await api.GET("/api/invitations", { params })
    : { data: [] };
  const { error } = await searchParams;

  // Admins can't touch owners or hand out the owner role (the API enforces it too).
  const editable = (role: string) => canManage && (isOwner || role !== "owner");
  const assignable = isOwner ? ROLES : ROLES.filter((r) => r !== "owner");

  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">Members of {tenant.name}</h1>
      {typeof error === "string" && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Team</CardTitle>
        </CardHeader>
        <CardContent>
          <ul className="divide-y">
            {members.map((m) => (
              <li key={m.user_id} className="flex flex-wrap items-center gap-3 py-3" data-member={m.email}>
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium">{m.name || m.email}</p>
                  {m.name && <p className="truncate text-xs text-muted-foreground">{m.email}</p>}
                </div>
                {editable(m.role) ? (
                  <>
                    <form action={changeRole} className="flex items-center gap-2">
                      <input type="hidden" name="tenant_id" value={tenantId} />
                      <input type="hidden" name="user_id" value={m.user_id} />
                      <select
                        name="role"
                        defaultValue={m.role}
                        aria-label={`Role of ${m.email}`}
                        className="h-8 rounded-md border bg-background px-2 text-sm"
                      >
                        {assignable.map((r) => (
                          <option key={r} value={r}>
                            {r}
                          </option>
                        ))}
                      </select>
                      <Button type="submit" size="sm" variant="outline">
                        Save
                      </Button>
                    </form>
                    <form action={removeMember}>
                      <input type="hidden" name="tenant_id" value={tenantId} />
                      <input type="hidden" name="user_id" value={m.user_id} />
                      <Button type="submit" size="sm" variant="ghost">
                        Remove
                      </Button>
                    </form>
                  </>
                ) : (
                  <span className="text-sm text-muted-foreground">{m.role}</span>
                )}
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>

      {canManage && (
        <Card>
          <CardHeader>
            <CardTitle>Invite someone</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <InviteForm tenantId={tenantId} canInviteOwners={isOwner} />
            {invitations && invitations.length > 0 && (
              <ul className="divide-y border-t pt-2">
                {invitations.map((inv) => (
                  <li key={inv.id} className="flex items-center justify-between gap-3 py-2 text-sm">
                    <span>
                      {inv.email} · {inv.role} · <span className="text-muted-foreground">pending</span>
                    </span>
                    <form action={revokeInvitation}>
                      <input type="hidden" name="tenant_id" value={tenantId} />
                      <input type="hidden" name="invitation_id" value={inv.id} />
                      <Button type="submit" size="sm" variant="ghost">
                        Revoke
                      </Button>
                    </form>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      )}
    </>
  );
}
