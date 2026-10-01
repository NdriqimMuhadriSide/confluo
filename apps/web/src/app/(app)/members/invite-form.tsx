"use client";

import { useActionState } from "react";

import { inviteMember } from "@/app/(app)/actions";
import { FormStatus } from "@/components/form-status";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function InviteForm({ tenantId, canInviteOwners }: { tenantId: string; canInviteOwners: boolean }) {
  const [state, action, pending] = useActionState(inviteMember, {});
  return (
    <form action={action} className="flex flex-col gap-3">
      <input type="hidden" name="tenant_id" value={tenantId} />
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <div className="grid flex-1 gap-2">
          <Label htmlFor="invite-email">Email</Label>
          <Input id="invite-email" name="email" type="email" required />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="invite-role">Role</Label>
          <select id="invite-role" name="role" defaultValue="staff" className="h-9 rounded-md border bg-background px-2 text-sm">
            <option value="staff">Staff</option>
            <option value="admin">Admin</option>
            {canInviteOwners && <option value="owner">Owner</option>}
          </select>
        </div>
        <Button type="submit" disabled={pending}>
          {pending ? "Inviting…" : "Invite"}
        </Button>
      </div>
      <FormStatus state={state} />
    </form>
  );
}
