import { createBrowserClient } from "@supabase/ssr";

import { cookieOptions, SUPABASE_PUBLISHABLE_KEY, SUPABASE_URL } from "./env";

export function createClient() {
  return createBrowserClient(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, { cookieOptions });
}
