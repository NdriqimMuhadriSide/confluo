"use client";

import { useTranslations } from "next-intl";
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
  const t = useTranslations("home");
  const [state, action, pending] = useActionState(createTenant, {});
  const [slug, setSlug] = useState("");
  const [slugEdited, setSlugEdited] = useState(false);

  // After a business is created the form starts over for the next one
  // (adjusting state while rendering when the action result changes).
  const [seen, setSeen] = useState(state);
  if (state !== seen) {
    setSeen(state);
    if (state.message) {
      setSlug("");
      setSlugEdited(false);
    }
  }

  return (
    <form action={action} className="flex flex-col gap-4">
      <div className="grid gap-2">
        <Label htmlFor="name">{t("businessName")}</Label>
        <Input id="name" name="name" required maxLength={120} onChange={(e) => !slugEdited && setSlug(slugify(e.target.value))} />
      </div>
      <div className="grid gap-2">
        <Label htmlFor="slug">{t("shortName")}</Label>
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
        <p className="text-xs text-muted-foreground">{t("shortNameHint")}</p>
      </div>
      <FormStatus state={state} />
      <Button type="submit" disabled={pending} className="self-start" data-testid="create-business">
        {pending ? t("creating") : t("create")}
      </Button>
    </form>
  );
}
