import type { Metadata } from "next";
import Link from "next/link";
import { getFormatter, getTranslations } from "next-intl/server";

import { AutoRefresh } from "@/components/auto-refresh";
import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { apiClient, inTenant, type Schemas } from "@/lib/api/client";
import { requireModule } from "@/lib/session";
import { cn } from "@/lib/utils";

import { handBack, replyToConversation } from "../crm-actions";

export const metadata: Metadata = { title: "Inbox · Confluo" };

type Message = Schemas["MessageOut"];

const BUBBLE: Record<Message["sender_type"], string> = {
  customer: "mr-auto bg-muted",
  ai: "ml-auto bg-violet-100 text-violet-950 dark:bg-violet-900 dark:text-violet-50",
  staff: "ml-auto bg-primary text-primary-foreground",
  system: "mx-auto bg-emerald-50 text-emerald-900 text-xs dark:bg-emerald-950 dark:text-emerald-100",
};

export default async function InboxPage({ searchParams }: PageProps<"/inbox">) {
  const params = await searchParams;
  const t = await getTranslations("crm.inbox");
  const format = await getFormatter();
  const { tenantId } = await requireModule("crm");
  const api = await apiClient();
  const header = inTenant(tenantId);
  const { data: conversations } = await api.GET("/api/crm/conversations", { params: { header } });
  const selected = typeof params.c === "string" ? params.c : conversations?.[0]?.id;
  const { data: detail } = selected
    ? await api.GET("/api/crm/conversations/{conversation_id}", { params: { header, path: { conversation_id: selected } } })
    : { data: undefined };
  const when = (s: string | null | undefined) => (s ? format.relativeTime(new Date(s)) : "");

  return (
    <>
      <AutoRefresh />
      <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
      <Notice params={params} />
      {(conversations ?? []).length === 0 ? (
        <p className="text-muted-foreground">{t("empty")}</p>
      ) : (
        <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
          <Card className="py-0">
            <ul className="divide-y text-sm" data-testid="conversations">
              {(conversations ?? []).map((c) => (
                <li key={c.id}>
                  <Link
                    href={`/inbox?c=${c.id}`}
                    className={cn("block space-y-1 px-4 py-3 hover:bg-muted/50", c.id === selected && "bg-muted")}
                    data-conversation={c.customer_name ?? ""}
                  >
                    <div className="flex items-center gap-2">
                      <span className="min-w-0 flex-1 truncate font-medium">{c.customer_name ?? t("visitor")}</span>
                      <span className="text-xs text-muted-foreground">{when(c.last_message_at)}</span>
                    </div>
                    <div className="truncate text-muted-foreground">{c.last_message}</div>
                    <div className="flex flex-wrap gap-1 text-xs">
                      <span className="rounded bg-muted px-1.5 py-0.5">{c.channel}</span>
                      <span className={cn("rounded px-1.5 py-0.5", c.handler === "ai" ? "bg-violet-100 text-violet-800 dark:bg-violet-900 dark:text-violet-100" : "bg-amber-100 text-amber-900 dark:bg-amber-900 dark:text-amber-100")}>
                        {c.handler === "ai" ? t("aiHandles") : t("staffHandles")}
                      </span>
                      {c.appointments > 0 && <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-emerald-900 dark:bg-emerald-900 dark:text-emerald-100">{t("booked", { count: c.appointments })}</span>}
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          </Card>
          {detail && (
            <Card>
              <CardContent className="space-y-4">
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="flex-1 font-semibold">{detail.customer_name ?? t("visitor")}</h2>
                  {detail.language && <span className="text-xs uppercase text-muted-foreground">{detail.language}</span>}
                  {detail.handler === "human" && (
                    <form action={handBack}>
                      <input type="hidden" name="tenant_id" value={tenantId} />
                      <input type="hidden" name="id" value={detail.id} />
                      <Button type="submit" size="sm" variant="outline">
                        {t("handBack")}
                      </Button>
                    </form>
                  )}
                </div>
                {detail.handler === "human" && detail.status === "waiting" && <p className="rounded-md bg-amber-50 p-2 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-100">{t("waiting")}</p>}
                <ol className="flex flex-col gap-2" data-testid="messages">
                  {detail.messages.map((m) => (
                    <li key={m.id} className={cn("max-w-[85%] rounded-lg px-3 py-2 text-sm whitespace-pre-line", BUBBLE[m.sender_type])} data-from={m.sender_type}>
                      {m.sender_type !== "customer" && <div className="mb-0.5 text-[11px] opacity-70">{t(`from.${m.sender_type}`)}</div>}
                      {m.body}
                    </li>
                  ))}
                </ol>
                <form action={replyToConversation} className="flex gap-2">
                  <input type="hidden" name="tenant_id" value={tenantId} />
                  <input type="hidden" name="id" value={detail.id} />
                  <textarea name="text" required rows={2} placeholder={t("replyPlaceholder")} className="min-w-0 flex-1 rounded-md border bg-background px-3 py-2 text-sm" />
                  <Button type="submit" className="self-end">
                    {t("send")}
                  </Button>
                </form>
                <p className="text-xs text-muted-foreground">{t("replyHint")}</p>
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </>
  );
}
