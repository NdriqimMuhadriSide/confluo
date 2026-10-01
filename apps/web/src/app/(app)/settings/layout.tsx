import { getTranslations } from "next-intl/server";

import { SettingsTabs } from "@/components/settings-tabs";

export default async function SettingsLayout({ children }: LayoutProps<"/settings">) {
  const t = await getTranslations("settings");
  const tabs = (["modules", "usage", "activity", "system"] as const).map((key) => ({
    href: `/settings/${key}`,
    label: t(`tabs.${key}`),
  }));
  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
      <SettingsTabs tabs={tabs} />
      {children}
    </>
  );
}
