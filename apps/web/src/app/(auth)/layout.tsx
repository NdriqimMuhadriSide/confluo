import { getTranslations } from "next-intl/server";

import { LanguageSwitcher } from "@/components/language-switcher";

export default async function AuthLayout({ children }: LayoutProps<"/">) {
  const t = await getTranslations("common");
  return (
    <div className="flex flex-1 flex-col">
      <div className="flex justify-end p-3">
        <LanguageSwitcher />
      </div>
      <main className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center gap-6 p-6">
        <p className="text-center text-lg font-semibold tracking-tight">{t("appName")}</p>
        {children}
      </main>
    </div>
  );
}
