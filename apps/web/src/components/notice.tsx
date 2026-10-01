import { getTranslations } from "next-intl/server";

// The ?saved / ?error notice the settings actions redirect back with.
export async function Notice({ params }: { params: Record<string, string | string[] | undefined> }) {
  const t = await getTranslations("setup");
  if (typeof params.error === "string") {
    return (
      <p role="alert" className="text-sm text-destructive">
        {params.error}
      </p>
    );
  }
  if (params.saved) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        {t("saved")}
      </p>
    );
  }
  return null;
}
