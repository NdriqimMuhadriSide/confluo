// Public Supabase settings, safe to ship to the browser: the publishable key only
// allows Auth calls, because the Data API is disabled on the project.
export const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL || "http://127.0.0.1:54321";
export const SUPABASE_PUBLISHABLE_KEY = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY || "";

// Where the Next.js server reaches Supabase. Differs from SUPABASE_URL only when the
// server runs in a container (`make up`), where 127.0.0.1 is the container itself.
export const SUPABASE_SERVER_URL = process.env.SUPABASE_INTERNAL_URL || SUPABASE_URL;

// Fixed cookie name: by default it is derived from the URL, which would differ between
// the browser and server clients when SUPABASE_SERVER_URL is set.
export const cookieOptions = { name: "sb-confluo-auth-token" };
