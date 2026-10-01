"use client";

import { useTranslations } from "next-intl";
import { useActionState } from "react";

import { inviteMember } from "@/app/(app)/actions";
import { FormStatus } from "@/components/form-status";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function InviteForm({ tenantId, canInviteOwners }: { tenantId: string; canInviteOwners: boolean }) {
  const t = useTranslations();
  const [state, action, pending] = useActionState(inviteMember, {});
  const roles = canInviteOwners ? ["staff", "admin", "owner"] : ["staff", "admin"];
  return (
    <form action={action} className="flex flex-col gap-3">
      <input type="hidden" name="tenant_id" value={tenantId} />
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <div className="grid flex-1 gap-2">
          <Label htmlFor="invite-email">{t("auth.email")}</Label>
          <Input id="invite-email" name="email" type="email" required />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="invite-role">{t("members.role")}</Label>
          <select id="invite-role" name="role" defaultValue="staff" className="h-9 rounded-md border bg-background px-2 text-sm">
            {roles.map((r) => (
              <option key={r} value={r}>
                {t(`common.roles.${r}`)}
              </option>
            ))}
          </select>
        </div>
        <Button type="submit" disabled={pending} data-testid="invite">
          {pending ? t("members.inviting") : t("members.inviteButton")}
        </Button>
      </div>
      <FormStatus state={state} />
    </form>
  );
}
