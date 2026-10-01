"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { useActionState } from "react";

import { signUp } from "@/app/(auth)/actions";
import { FormStatus } from "@/components/form-status";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function SignupForm() {
  const t = useTranslations("auth");
  const [state, action, pending] = useActionState(signUp, {});
  return (
    <form className="flex flex-col gap-4" action={action}>
      <div className="grid gap-2">
        <Label htmlFor="name">{t("name")}</Label>
        <Input id="name" name="name" autoComplete="name" required />
      </div>
      <div className="grid gap-2">
        <Label htmlFor="email">{t("email")}</Label>
        <Input id="email" name="email" type="email" autoComplete="email" defaultValue={state.email} required />
      </div>
      <div className="grid gap-2">
        <Label htmlFor="password">{t("password")}</Label>
        <Input id="password" name="password" type="password" autoComplete="new-password" minLength={8} required />
      </div>
      <FormStatus state={state} />
      <Button type="submit" disabled={pending} data-testid="create-account">
        {pending ? t("creatingAccount") : t("createAccount")}
      </Button>
      <p className="text-center text-sm text-muted-foreground">
        {t("haveAccount")}{" "}
        <Link href="/login" className="underline underline-offset-4">
          {t("signIn")}
        </Link>
      </p>
    </form>
  );
}
