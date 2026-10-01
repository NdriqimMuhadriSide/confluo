"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { useActionState } from "react";

import { type AuthState, logIn } from "@/app/(auth)/actions";
import { FormStatus } from "@/components/form-status";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function LoginForm({ next, linkError }: { next: string; linkError: boolean }) {
  const t = useTranslations("auth");
  const initial: AuthState = linkError ? { error: t("linkInvalid") } : {};
  const [state, action, pending] = useActionState(logIn, initial);

  return (
    <form className="flex flex-col gap-4" action={action}>
      <input type="hidden" name="next" value={next} />
      <div className="grid gap-2">
        <Label htmlFor="email">{t("email")}</Label>
        <Input id="email" name="email" type="email" autoComplete="email" defaultValue={state.email} required />
      </div>
      <div className="grid gap-2">
        <Label htmlFor="password">{t("password")}</Label>
        <Input id="password" name="password" type="password" autoComplete="current-password" />
      </div>
      <FormStatus state={state} />
      <Button type="submit" name="intent" value="password" disabled={pending} data-testid="sign-in">
        {t("signIn")}
      </Button>
      <Button type="submit" name="intent" value="magic-link" variant="outline" disabled={pending} data-testid="magic-link">
        {t("magicLink")}
      </Button>
      <p className="text-center text-sm text-muted-foreground">
        {t("noAccount")}{" "}
        <Link href="/signup" className="underline underline-offset-4">
          {t("signUp")}
        </Link>
      </p>
    </form>
  );
}
