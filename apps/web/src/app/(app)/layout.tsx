import Link from "next/link";
import { connection } from "next/server";

import { signOut } from "@/app/(auth)/actions";
import { TenantSwitcher } from "@/components/tenant-switcher";
import { Button } from "@/components/ui/button";
import { currentTenantId, getMe } from "@/lib/api/client";

export default async function AppLayout({ children }: LayoutProps<"/">) {
  await connection();
  const me = await getMe();
  const tenantId = me ? await currentTenantId(me) : null;

  return (
    <div className="flex min-h-full flex-1 flex-col">
      <header className="flex items-center gap-4 border-b px-4 py-2">
        <span className="font-semibold tracking-tight">Confluo</span>
        {me && tenantId && (
          <>
            <TenantSwitcher tenants={me.tenants} current={tenantId} />
            <nav className="flex gap-3 text-sm">
              <Link href="/" className="hover:underline">
                Home
              </Link>
              <Link href="/members" className="hover:underline">
                Members
              </Link>
            </nav>
          </>
        )}
        <div className="ml-auto flex items-center gap-3 text-sm text-muted-foreground">
          <span className="hidden sm:inline">{me?.email}</span>
          <form action={signOut}>
            <Button type="submit" variant="outline" size="sm">
              Sign out
            </Button>
          </form>
        </div>
      </header>
      <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-6 p-4 sm:p-6">
        {children}
      </main>
    </div>
  );
}
