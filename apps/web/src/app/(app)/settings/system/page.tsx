import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { getFormatter, getTranslations } from "next-intl/server";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiClient, getHealth, getMe, inTenant } from "@/lib/api/client";
import { requireTenant } from "@/lib/session";

import { retryJob } from "./actions";

export const metadata: Metadata = { title: "System health · Confluo" };

// Stack status plus background jobs that used up their retries, with manual retry.
export default async function SystemHealthPage({ searchParams }: PageProps<"/settings/system">) {
  const t = await getTranslations("settings.system");
  const tc = await getTranslations("common");
  const format = await getFormatter();
  const { tenantId } = await requireTenant();
  const [{ data: jobs, response }, health, me] = await Promise.all([
    (await apiClient()).GET("/api/system/jobs", { params: { header: inTenant(tenantId) } }),
    getHealth(),
    getMe(),
  ]);
  if (response.status === 403 || !jobs) redirect("/");
  const { retried, error } = await searchParams;

  const rows: [string, boolean][] = [
    [t("api"), health !== null],
    [t("database"), health?.database ?? false],
    [t("session"), me !== null],
  ];

  return (
    <>
      {retried && (
        <p role="status" className="text-sm text-muted-foreground">
          {t("retried")}
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {t("retryError")}
        </p>
      )}
      <Card>
        <CardHeader>
          <CardTitle>{t("status")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          {rows.map(([name, ok]) => (
            <div key={name} className="flex items-center justify-between">
              <span>{name}</span>
              <span className={ok ? "text-emerald-600" : "text-red-600"}>{ok ? t("up") : t("down")}</span>
            </div>
          ))}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>{t("failedJobs")}</CardTitle>
        </CardHeader>
        <CardContent>
          {jobs.length === 0 ? (
            <p className="text-sm text-muted-foreground" data-health="ok">
              {t("allGood")}
            </p>
          ) : (
            <ul className="divide-y text-sm">
              {jobs.map((j) => (
                <li key={j.job_id} className="flex flex-wrap items-center gap-3 py-3" data-job={j.job_id}>
                  <div className="min-w-0 flex-1">
                    <p className="font-medium">{j.task_name}</p>
                    <p className="truncate text-muted-foreground">{j.error}</p>
                    <p className="text-xs text-muted-foreground">
                      {t("attempts", { count: j.attempts })} ·{" "}
                      {t("last", { when: format.dateTime(new Date(j.failed_at), { dateStyle: "short", timeStyle: "short", hourCycle: "h23" }) })}
                    </p>
                  </div>
                  <form action={retryJob}>
                    <input type="hidden" name="tenant_id" value={tenantId} />
                    <input type="hidden" name="job_id" value={j.job_id} />
                    <Button type="submit" size="sm" variant="outline">
                      {tc("retry")}
                    </Button>
                  </form>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </>
  );
}
