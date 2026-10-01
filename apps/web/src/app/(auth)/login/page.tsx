import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import { LoginForm } from "./login-form";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("auth");
  return { title: `${t("signInTitle")} · Confluo` };
}

export default async function LoginPage({ searchParams }: PageProps<"/login">) {
  const t = await getTranslations("auth");
  const params = await searchParams;
  const next = typeof params.next === "string" ? params.next : "/";
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("signInTitle")}</CardTitle>
      </CardHeader>
      <CardContent>
        <LoginForm next={next} linkError={params.error === "link"} />
      </CardContent>
    </Card>
  );
}
