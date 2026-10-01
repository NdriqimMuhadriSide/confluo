import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { getFormatter } from "next-intl/server";

import { Card, CardContent } from "@/components/ui/card";
import { apiClient, inTenant } from "@/lib/api/client";
import { requireTenant } from "@/lib/session";

export const metadata: Metadata = { title: "Activity · Confluo" };

function summary(diff: Record<string, unknown>): string {
  const fields = Object.keys((diff.new ?? diff.old ?? {}) as Record<string, unknown>);
  return fields.slice(0, 6).join(", ") + (fields.length > 6 ? ", …" : "");
}

// The tenant's audit trail: who changed what, newest first.
export default async function ActivityPage() {
  const format = await getFormatter();
  const { tenantId } = await requireTenant();
  const { data, response } = await (await apiClient()).GET("/api/audit", {
    params: { header: inTenant(tenantId), query: { limit: 100 } },
  });
  if (response.status === 403 || !data) redirect("/");

  return (
    <>
      <Card>
        <CardContent>
          <ul className="divide-y text-sm">
            {data.map((e) => (
              <li key={e.id} className="flex flex-wrap gap-x-3 gap-y-1 py-2" data-audit={`${e.action} ${e.entity}`}>
                <time className="w-36 shrink-0 text-muted-foreground" dateTime={e.at}>
                  {format.dateTime(new Date(e.at), { dateStyle: "short", timeStyle: "short", hourCycle: "h23" })}
                </time>
                <span className="font-medium">
                  {e.action} {e.entity}
                </span>
                <span className="text-muted-foreground">{summary(e.diff)}</span>
                <span className="ml-auto text-muted-foreground">
                  {e.actor_type === "user" ? (e.actor_email ?? "user") : e.actor_type}
                </span>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </>
  );
}
