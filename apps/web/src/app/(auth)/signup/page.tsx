import type { Metadata } from "next";
import { getTranslations } from "next-intl/server";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import { SignupForm } from "./signup-form";

export async function generateMetadata(): Promise<Metadata> {
  const t = await getTranslations("auth");
  return { title: `${t("signUpTitle")} · Confluo` };
}

export default async function SignupPage() {
  const t = await getTranslations("auth");
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("signUpTitle")}</CardTitle>
      </CardHeader>
      <CardContent>
        <SignupForm />
      </CardContent>
    </Card>
  );
}
