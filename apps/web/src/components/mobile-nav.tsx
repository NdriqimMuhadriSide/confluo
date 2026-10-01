"use client";

import { MenuIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { signOut } from "@/app/(auth)/actions";
import { LanguageSwitcher } from "@/components/language-switcher";
import { type NavLink, NavLinks } from "@/components/nav-links";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";

export function MobileNav({ links, email }: { links: NavLink[]; email: string | null }) {
  const t = useTranslations("common");
  const [open, setOpen] = useState(false);
  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger render={<Button variant="ghost" size="icon" className="md:hidden" aria-label={t("menu")} />}>
        <MenuIcon />
      </SheetTrigger>
      <SheetContent side="left" className="w-72 gap-6 p-4">
        <SheetTitle className="text-lg font-semibold">{t("appName")}</SheetTitle>
        <NavLinks links={links} onNavigate={() => setOpen(false)} />
        <div className="mt-auto flex flex-col gap-3 border-t pt-4">
          <LanguageSwitcher id="locale-mobile" />
          {email && <p className="truncate text-sm text-muted-foreground">{email}</p>}
          <form action={signOut}>
            <Button type="submit" variant="outline" className="w-full">
              {t("signOut")}
            </Button>
          </form>
        </div>
      </SheetContent>
    </Sheet>
  );
}
