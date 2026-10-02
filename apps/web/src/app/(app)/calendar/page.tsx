import type { Metadata } from "next";
import Link from "next/link";
import { getFormatter, getLocale, getTranslations } from "next-intl/server";

import { AutoRefresh } from "@/components/auto-refresh";
import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiClient, inTenant, type Schemas } from "@/lib/api/client";
import { localized } from "@/lib/localized";
import { requireModule } from "@/lib/session";
import { cn } from "@/lib/utils";

import { createAppointment, setAppointmentStatus } from "../crm-actions";

export const metadata: Metadata = { title: "Calendar · Confluo" };

type Appointment = Schemas["AppointmentOut"];

const SELECT = "h-9 rounded-md border bg-background px-2 text-sm";

function iso(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function monday(value?: string): Date {
  const d = value && /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T12:00:00`) : new Date();
  d.setHours(12, 0, 0, 0);
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
  return d;
}

function addDays(d: Date, n: number): Date {
  const out = new Date(d);
  out.setDate(out.getDate() + n);
  return out;
}

const STATUS_STYLE: Record<Appointment["status"], string> = {
  pending_approval: "border-amber-400 bg-amber-50 dark:bg-amber-950/40",
  confirmed: "border-emerald-500 bg-emerald-50 dark:bg-emerald-950/40",
  cancelled: "border-muted bg-muted/40 text-muted-foreground line-through",
  no_show: "border-muted bg-muted/40 text-muted-foreground",
  completed: "border-sky-400 bg-sky-50 dark:bg-sky-950/40",
};

export default async function CalendarPage({ searchParams }: PageProps<"/calendar">) {
  const params = await searchParams;
  const t = await getTranslations("crm.calendar");
  const format = await getFormatter();
  const locale = await getLocale();
  const { tenantId } = await requireModule("crm");
  const api = await apiClient();
  const header = inTenant(tenantId);
  const week = monday(typeof params.week === "string" ? params.week : undefined);
  const resource = typeof params.resource === "string" && params.resource ? params.resource : undefined;
  const here = `/calendar?week=${iso(week)}${resource ? `&resource=${resource}` : ""}`;

  const [{ data: calendar }, { data: resources }, { data: services }] = await Promise.all([
    api.GET("/api/crm/appointments", {
      params: { header, query: { start: iso(week), days: 7, resource_id: resource } },
    }),
    api.GET("/api/crm/resources", { params: { header } }),
    api.GET("/api/crm/services", { params: { header } }),
  ]);
  const tz = calendar?.timezone ?? "Europe/Brussels";
  const appointments = calendar?.appointments ?? [];
  const dayKey = new Intl.DateTimeFormat("en-CA", { timeZone: tz });
  const days = Array.from({ length: 7 }, (_, i) => addDays(week, i));
  const today = dayKey.format(new Date());
  const time = (s: string) => format.dateTime(new Date(s), { timeStyle: "short", hourCycle: "h23", timeZone: tz });
  const pending = appointments.filter((a) => a.status === "pending_approval");

  const statusForm = (a: Appointment, action: "approve" | "reject" | "cancel", variant: "default" | "outline" | "ghost") => (
    <form action={setAppointmentStatus}>
      <input type="hidden" name="tenant_id" value={tenantId} />
      <input type="hidden" name="id" value={a.id} />
      <input type="hidden" name="action" value={action} />
      <input type="hidden" name="back" value={here} />
      <Button type="submit" size="sm" variant={variant} data-testid={`${action}-${a.id}`}>
        {t(action)}
      </Button>
    </form>
  );

  return (
    <>
      <AutoRefresh />
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="flex-1 text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <form className="flex items-center gap-2" action="/calendar">
          <input type="hidden" name="week" value={iso(week)} />
          <select name="resource" defaultValue={resource ?? ""} className={SELECT} aria-label={t("who")}>
            <option value="">{t("everyone")}</option>
            {(resources ?? []).map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
          <Button type="submit" size="sm" variant="outline">
            {t("show")}
          </Button>
        </form>
        <div className="flex items-center gap-1 text-sm">
          <Link className="rounded-md border px-2 py-1" href={`/calendar?week=${iso(addDays(week, -7))}${resource ? `&resource=${resource}` : ""}`}>
            ←
          </Link>
          <Link className="rounded-md border px-2 py-1" href={`/calendar${resource ? `?resource=${resource}` : ""}`}>
            {t("thisWeek")}
          </Link>
          <Link className="rounded-md border px-2 py-1" href={`/calendar?week=${iso(addDays(week, 7))}${resource ? `&resource=${resource}` : ""}`}>
            →
          </Link>
        </div>
      </div>
      <Notice params={params} />

      {pending.length > 0 && (
        <Card className="border-amber-400" data-testid="pending">
          <CardHeader>
            <CardTitle>{t("pending", { count: pending.length })}</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="divide-y text-sm">
              {pending.map((a) => (
                <li key={a.id} className="flex flex-wrap items-center gap-3 py-2" data-pending={a.customer_name ?? ""}>
                  <span className="min-w-0 flex-1">
                    <span className="font-medium">{a.customer_name ?? t("unknownCustomer")}</span> · {localized(a.service_name, locale)} ·{" "}
                    {format.dateTime(new Date(a.start), { weekday: "short", day: "numeric", month: "short", timeZone: tz })} {time(a.start)} · {a.resource_name}
                    {a.source === "ai" && <span className="ml-2 rounded bg-violet-100 px-1.5 py-0.5 text-xs text-violet-800 dark:bg-violet-900 dark:text-violet-100">{t("viaAi")}</span>}
                  </span>
                  {a.conversation_id && (
                    <Link href={`/inbox?c=${a.conversation_id}`} className="text-muted-foreground underline underline-offset-4">
                      {t("chat")}
                    </Link>
                  )}
                  {statusForm(a, "approve", "default")}
                  {statusForm(a, "reject", "ghost")}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-2 md:grid-cols-7" data-testid="week" data-wide>
        {days.map((d) => {
          const key = iso(d);
          const items = appointments.filter((a) => dayKey.format(new Date(a.start)) === key);
          return (
            <section key={key} className={cn("min-h-32 rounded-lg border p-2", key === today && "border-primary")} data-day={key}>
              <h2 className="mb-2 text-sm font-medium">
                {format.dateTime(d, { weekday: "short", day: "numeric", month: "short" })}
              </h2>
              {items.length === 0 ? (
                <p className="text-xs text-muted-foreground">—</p>
              ) : (
                <ul className="space-y-2">
                  {items.map((a) => (
                    <li key={a.id} className={cn("rounded-md border-l-4 p-2 text-xs", STATUS_STYLE[a.status])} data-appointment={a.customer_name ?? ""} data-status={a.status}>
                      <div className="font-medium whitespace-nowrap">
                        {time(a.start)}–{time(a.end)}
                      </div>
                      <div>{localized(a.service_name, locale)}</div>
                      <div>{a.customer_name ?? t("unknownCustomer")}</div>
                      <div className="text-muted-foreground">
                        {a.resource_name}
                        {a.source === "ai" && ` · ${t("viaAi")}`}
                      </div>
                      {a.status === "pending_approval" && <div className="mt-1 font-medium text-amber-700 dark:text-amber-300">{t("status.pending_approval")}</div>}
                      {a.status === "confirmed" && (
                        <form action={setAppointmentStatus} className="mt-1">
                          <input type="hidden" name="tenant_id" value={tenantId} />
                          <input type="hidden" name="id" value={a.id} />
                          <input type="hidden" name="action" value="cancel" />
                          <input type="hidden" name="back" value={here} />
                          <button type="submit" className="text-muted-foreground underline-offset-2 hover:text-destructive hover:underline" data-testid={`cancel-${a.id}`}>
                            {t("cancel")}
                          </button>
                        </form>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          );
        })}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>{t("new")}</CardTitle>
        </CardHeader>
        <CardContent>
          <form action={createAppointment} className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6 lg:items-end">
            <input type="hidden" name="tenant_id" value={tenantId} />
            <input type="hidden" name="back" value={here} />
            <div className="grid gap-1.5">
              <Label htmlFor="new-service">{t("service")}</Label>
              <select id="new-service" name="service_id" required className={SELECT}>
                {(services ?? [])
                  .filter((s) => s.active)
                  .map((s) => (
                    <option key={s.id} value={s.id}>
                      {localized(s.name_i18n, locale)}
                    </option>
                  ))}
              </select>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="new-resource">{t("who")}</Label>
              <select id="new-resource" name="resource_id" required defaultValue={resource} className={SELECT}>
                {(resources ?? [])
                  .filter((r) => r.active)
                  .map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.name}
                    </option>
                  ))}
              </select>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="new-date">{t("date")}</Label>
              <Input id="new-date" name="date" type="date" required defaultValue={today} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="new-time">{t("time")}</Label>
              <Input id="new-time" name="time" type="time" required step={900} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="new-customer">{t("customer")}</Label>
              <Input id="new-customer" name="customer_name" required />
            </div>
            <Button type="submit" data-testid="create-appointment">
              {t("book")}
            </Button>
          </form>
        </CardContent>
      </Card>
    </>
  );
}
