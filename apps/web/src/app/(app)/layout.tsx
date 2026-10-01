import { connection } from "next/server";
import { getTranslations } from "next-intl/server";

import { signOut } from "@/app/(auth)/actions";
import { LanguageSwitcher } from "@/components/language-switcher";
import { MobileNav } from "@/components/mobile-nav";
import { type NavLink, NavLinks } from "@/components/nav-links";
import { TenantSwitcher } from "@/components/tenant-switcher";
import { Button } from "@/components/ui/button";
import { currentTenantId, getManifest, getMe } from "@/lib/api/client";

// The signed-in shell: top bar (business switcher, account) on every screen size,
// a left sidebar with the nav from md up, and the same nav in a sheet on phones.
export default async function AppLayout({ children }: LayoutProps<"/">) {
  await connection();
  const t = await getTranslations();
  const me = await getMe();
  const tenantId = me ? await currentTenantId(me) : null;
  const manifest = tenantId ? await getManifest(tenantId) : null;

  const links: NavLink[] = [{ href: "/", label: t("nav.home") }];
  if (manifest) {
    for (const item of manifest.nav) {
      const key = `nav.${item.module}.${item.key}`;
      links.push({ href: item.href, label: t.has(key) ? t(key) : item.label });
    }
    links.push({ href: "/members", label: t("nav.members") });
    if (manifest.permissions.includes("core.modules.manage")) {
      links.push({ href: "/settings", label: t("nav.settings") });
    }
  }

  return (
    <div className="flex min-h-full flex-1 flex-col" data-tenant-id={tenantId ?? undefined}>
      <header className="sticky top-0 z-30 flex h-14 items-center gap-2 border-b bg-background px-3 md:px-4">
        {tenantId && <MobileNav links={links} email={me?.email ?? null} />}
        <span className="shrink-0 font-semibold tracking-tight">{t("common.appName")}</span>
        {me && tenantId && (
          <div className="min-w-0 max-w-56 flex-1 md:flex-none">
            <TenantSwitcher tenants={me.tenants} current={tenantId} />
          </div>
        )}
        <div className="ml-auto hidden items-center gap-3 text-sm text-muted-foreground md:flex">
          <LanguageSwitcher />
          <span data-testid="user-email">{me?.email}</span>
          <form action={signOut}>
            <Button type="submit" variant="outline" size="sm">
              {t("common.signOut")}
            </Button>
          </form>
        </div>
        {!tenantId && (
          <div className="ml-auto flex items-center gap-2 md:hidden">
            <LanguageSwitcher id="locale-top" />
            <form action={signOut}>
              <Button type="submit" variant="outline" size="sm">
                {t("common.signOut")}
              </Button>
            </form>
          </div>
        )}
      </header>
      <div className="flex flex-1">
        {tenantId && (
          <aside className="hidden w-56 shrink-0 border-r bg-background p-3 md:block">
            <NavLinks links={links} />
          </aside>
        )}
        <main className="mx-auto flex w-full min-w-0 max-w-4xl flex-1 flex-col gap-6 p-4 md:p-8">
          {children}
        </main>
      </div>
    </div>
  );
}
