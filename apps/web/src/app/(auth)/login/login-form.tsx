"use client";

import Link from "next/link";
import { useActionState } from "react";

import { type AuthState, logIn } from "@/app/(auth)/actions";
import { FormStatus } from "@/components/form-status";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function LoginForm({ next, linkError }: { next: string; linkError: boolean }) {
  const initial: AuthState = linkError
    ? { error: "That sign-in link is invalid or has expired. Request a new one." }
    : {};
  const [state, action, pending] = useActionState(logIn, initial);

  return (
    <form className="flex flex-col gap-4" action={action}>
      <input type="hidden" name="next" value={next} />
      <div className="grid gap-2">
        <Label htmlFor="email">Email</Label>
        <Input
          id="email"
          name="email"
          type="email"
          autoComplete="email"
          defaultValue={state.email}
          required
        />
      </div>
      <div className="grid gap-2">
        <Label htmlFor="password">Password</Label>
        <Input id="password" name="password" type="password" autoComplete="current-password" />
      </div>
      <FormStatus state={state} />
      <Button type="submit" name="intent" value="password" disabled={pending}>
        Sign in
      </Button>
      <Button type="submit" name="intent" value="magic-link" variant="outline" disabled={pending}>
        Email me a sign-in link
      </Button>
      <p className="text-center text-sm text-muted-foreground">
        No account yet?{" "}
        <Link href="/signup" className="underline underline-offset-4">
          Sign up
        </Link>
      </p>
    </form>
  );
}
