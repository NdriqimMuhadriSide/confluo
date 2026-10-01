import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";

import { cookieOptions, SUPABASE_PUBLISHABLE_KEY, SUPABASE_SERVER_URL } from "./env";

// One client per request (Server Components, Server Actions, Route Handlers).
export async function createClient() {
  const cookieStore = await cookies();
  return createServerClient(SUPABASE_SERVER_URL, SUPABASE_PUBLISHABLE_KEY, {
    cookieOptions,
    cookies: {
      getAll: () => cookieStore.getAll(),
      setAll(cookiesToSet) {
        try {
          cookiesToSet.forEach(({ name, value, options }) => cookieStore.set(name, value, options));
        } catch {
          // Server Components can't set cookies; the proxy refreshes the session instead.
        }
      },
    },
  });
}

// The signed-in user's access token, for calls to the Confluo API.
export async function getAccessToken(): Promise<string | null> {
  const supabase = await createClient();
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? null;
}
