import { getTranslations } from "next-intl/server";

export async function ComingSoon({ titleKey }: { titleKey: string }) {
  const t = await getTranslations();
  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">{t(titleKey)}</h1>
      <p className="text-muted-foreground">{t("common.comingSoon")}</p>
    </>
  );
}
