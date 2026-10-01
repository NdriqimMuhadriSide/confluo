"use client";

import { useActionState, useState } from "react";

import { createTenant } from "@/app/(app)/actions";
import { FormStatus } from "@/components/form-status";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

function slugify(name: string): string {
  return name
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
}

export function CreateTenantForm() {
  const [state, action, pending] = useActionState(createTenant, {});
  const [slug, setSlug] = useState("");
  const [slugEdited, setSlugEdited] = useState(false);

  return (
    <form action={action} className="flex flex-col gap-4">
      <div className="grid gap-2">
        <Label htmlFor="name">Business name</Label>
        <Input
          id="name"
          name="name"
          required
          maxLength={120}
          onChange={(e) => !slugEdited && setSlug(slugify(e.target.value))}
        />
      </div>
      <div className="grid gap-2">
        <Label htmlFor="slug">Short name</Label>
        <Input
          id="slug"
          name="slug"
          required
          pattern="[a-z0-9](-?[a-z0-9])+"
          maxLength={40}
          value={slug}
          onChange={(e) => {
            setSlugEdited(true);
            setSlug(e.target.value);
          }}
        />
        <p className="text-xs text-muted-foreground">Lowercase letters, digits and dashes.</p>
      </div>
      <FormStatus state={state} />
      <Button type="submit" disabled={pending}>
        {pending ? "Creating…" : "Create business"}
      </Button>
    </form>
  );
}
