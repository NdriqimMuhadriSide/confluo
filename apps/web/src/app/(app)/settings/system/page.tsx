import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiClient, inTenant } from "@/lib/api/client";
import { requireTenant } from "@/lib/session";

import { retryJob } from "./actions";

export const metadata: Metadata = { title: "System health · Confluo" };

// Background jobs that used up their retries, with a manual retry.
export default async function SystemHealthPage({ searchParams }: PageProps<"/settings/system">) {
  const { tenantId } = await requireTenant();
  const { data: jobs, response } = await (await apiClient()).GET("/api/system/jobs", {
    params: { header: inTenant(tenantId) },
  });
  if (response.status === 403 || !jobs) redirect("/");
  const { retried, error } = await searchParams;

  return (
    <>
      <h1 className="text-2xl font-semibold tracking-tight">System health</h1>
      {retried && (
        <p role="status" className="text-sm text-muted-foreground">
          Queued for another try.
        </p>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          That job can no longer be retried.
        </p>
      )}
      <Card>
        <CardHeader>
          <CardTitle>Failed background jobs</CardTitle>
        </CardHeader>
        <CardContent>
          {jobs.length === 0 ? (
            <p className="text-sm text-muted-foreground" data-health="ok">
              Everything is running. No failed jobs.
            </p>
          ) : (
            <ul className="divide-y text-sm">
              {jobs.map((j) => (
                <li key={j.job_id} className="flex flex-wrap items-center gap-3 py-3" data-job={j.job_id}>
                  <div className="min-w-0 flex-1">
                    <p className="font-medium">{j.task_name}</p>
                    <p className="truncate text-muted-foreground">{j.error}</p>
                    <p className="text-xs text-muted-foreground">
                      {j.attempts} attempts · last{" "}
                      {new Date(j.failed_at).toLocaleString("en-GB", { dateStyle: "short", timeStyle: "short" })}
                    </p>
                  </div>
                  <form action={retryJob}>
                    <input type="hidden" name="tenant_id" value={tenantId} />
                    <input type="hidden" name="job_id" value={j.job_id} />
                    <Button type="submit" size="sm" variant="outline">
                      Retry
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
