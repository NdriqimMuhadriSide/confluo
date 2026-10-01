import type { EmailOtpType } from "@supabase/supabase-js";
import { type NextRequest, NextResponse } from "next/server";

import { redirectUrl } from "@/lib/origin";
import { createClient } from "@/lib/supabase/server";

// Landing route for links in auth emails (magic link, sign-up confirmation).
// Handles both the PKCE `code` flow and `token_hash` links from custom templates.
export async function GET(request: NextRequest) {
  const { searchParams } = request.nextUrl;
  const next = safeNext(searchParams.get("next"));
  const supabase = await createClient();

  const code = searchParams.get("code");
  const tokenHash = searchParams.get("token_hash");
  const type = searchParams.get("type") as EmailOtpType | null;

  const { error } = code
    ? await supabase.auth.exchangeCodeForSession(code)
    : tokenHash && type
      ? await supabase.auth.verifyOtp({ token_hash: tokenHash, type })
      : { error: new Error("missing code") };

  if (error) {
    console.warn("auth link rejected:", error.message);
    return NextResponse.redirect(redirectUrl(request, "/login?error=link"));
  }
  return NextResponse.redirect(redirectUrl(request, next));
}

// Only same-site relative paths, so the link can't redirect off-site.
function safeNext(next: string | null): string {
  return next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
}
