"use server";

import { headers } from "next/headers";
import { redirect } from "next/navigation";

import { originFromHeaders } from "@/lib/origin";
import { createClient } from "@/lib/supabase/server";

// `email` is echoed back so the form keeps it: React resets form fields after an action.
export type AuthState = { error?: string; message?: string; email?: string };

function field(form: FormData, name: string): string {
  return String(form.get(name) ?? "").trim();
}

function safeNext(next: string): string {
  return next.startsWith("/") && !next.startsWith("//") ? next : "/";
}

async function origin(): Promise<string> {
  return originFromHeaders(await headers());
}

// The login form has two submit buttons; `intent` says which one was pressed.
export async function logIn(state: AuthState, form: FormData): Promise<AuthState> {
  return form.get("intent") === "magic-link" ? sendMagicLink(state, form) : signIn(state, form);
}

async function signIn(_: AuthState, form: FormData): Promise<AuthState> {
  const email = field(form, "email");
  const supabase = await createClient();
  const { error } = await supabase.auth.signInWithPassword({
    email,
    password: String(form.get("password") ?? ""),
  });
  if (error) return { error: "Wrong email or password.", email };
  redirect(safeNext(field(form, "next")));
}

async function sendMagicLink(_: AuthState, form: FormData): Promise<AuthState> {
  const email = field(form, "email");
  if (!email) return { error: "Enter your email address first." };
  const supabase = await createClient();
  const next = encodeURIComponent(safeNext(field(form, "next")));
  const { error } = await supabase.auth.signInWithOtp({
    email,
    options: {
      shouldCreateUser: false,
      emailRedirectTo: `${await origin()}/auth/confirm?next=${next}`,
    },
  });
  // Same answer whether or not the account exists, so the form can't be used to
  // probe which emails are registered.
  if (error && error.status !== 400 && error.status !== 422) {
    return { error: "Could not send the link. Try again in a minute.", email };
  }
  return { message: `If ${email} has an account, a sign-in link is on its way.`, email };
}

export async function signUp(_: AuthState, form: FormData): Promise<AuthState> {
  const password = String(form.get("password") ?? "");
  const email = field(form, "email");
  if (password.length < 8) {
    return { error: "Use at least 8 characters for the password.", email };
  }
  const supabase = await createClient();
  const { data, error } = await supabase.auth.signUp({
    email,
    password,
    options: {
      data: { name: field(form, "name") },
      emailRedirectTo: `${await origin()}/auth/confirm`,
    },
  });
  if (error) return { error: error.message, email };
  if (!data.session) {
    return { message: "Check your email to confirm your account, then sign in.", email };
  }
  redirect("/");
}

export async function signOut(): Promise<void> {
  const supabase = await createClient();
  await supabase.auth.signOut();
  redirect("/login");
}
