import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { getFormatter, getTranslations } from "next-intl/server";

import { Card, CardContent } from "@/components/ui/card";
import { apiClient, inTenant } from "@/lib/api/client";
import { requireTenant } from "@/lib/session";

export const metadata: Metadata = { title: "AI usage · Confluo" };

const DAYS = 30;

export default async function UsagePage() {
  const t = await getTranslations("settings.usage");
  const format = await getFormatter();
  const { tenantId } = await requireTenant();
  const { data, response } = await (await apiClient()).GET("/api/usage", {
    params: { header: inTenant(tenantId), query: { days: DAYS } },
  });
  if (response.status === 403 || !data) redirect("/");
  const usd = (v: string | number) => format.number(Number(v), { style: "currency", currency: "USD", maximumFractionDigits: 4 });

  return (
    <>
      <p className="text-sm text-muted-foreground">{t("intro", { days: DAYS })}</p>
      <p className="text-lg font-semibold" data-testid="usage-total">
        {t("total")}: {usd(data.total_cost_usd)}
      </p>
      <Card>
        <CardContent className="overflow-x-auto">
          {data.rows.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("none")}</p>
          ) : (
            <table className="w-full text-left text-sm">
              <thead className="text-muted-foreground">
                <tr>
                  <th className="py-2 pr-3 font-medium">{t("day")}</th>
                  <th className="py-2 pr-3 font-medium">{t("model")}</th>
                  <th className="py-2 pr-3 font-medium">{t("purpose")}</th>
                  <th className="py-2 pr-3 text-right font-medium">{t("calls")}</th>
                  <th className="py-2 pr-3 text-right font-medium">{t("tokens")}</th>
                  <th className="py-2 text-right font-medium">USD</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {data.rows.map((r) => (
                  <tr key={`${r.day}-${r.tier}-${r.model}-${r.purpose}`}>
                    <td className="py-2 pr-3 whitespace-nowrap">{format.dateTime(new Date(r.day), { dateStyle: "short" })}</td>
                    <td className="py-2 pr-3">{r.model}</td>
                    <td className="py-2 pr-3">{r.purpose}</td>
                    <td className="py-2 pr-3 text-right">{format.number(r.calls)}</td>
                    <td className="py-2 pr-3 text-right whitespace-nowrap">
                      {format.number(r.input_tokens)} / {format.number(r.output_tokens)}
                    </td>
                    <td className="py-2 text-right">{r.cost_usd === null ? "—" : usd(r.cost_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </>
  );
}
