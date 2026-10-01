import { notFound } from "next/navigation";
import type { DateTimeFormatOptions } from "next-intl";
import { getFormatter, getTranslations } from "next-intl/server";

import { DAYS, type Day, HoursEditor } from "@/components/hours-editor";
import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiClient, inTenant } from "@/lib/api/client";
import { requireTenant } from "@/lib/session";

import { addDayOff, connectOutlook, deleteDayOff, disconnectCalendar, saveSchedule, syncCalendar } from "../../setup-actions";

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

export default async function ResourcePage({ params, searchParams }: PageProps<"/settings/team/[id]">) {
  const { id } = await params;
  const t = await getTranslations("setup");
  const tc = await getTranslations("common");
  const tcal = await getTranslations("setup.calendar");
  const format = await getFormatter();
  const { tenantId } = await requireTenant();
  const api = await apiClient();
  const header = inTenant(tenantId);
  const { data: resources } = await api.GET("/api/crm/resources", { params: { header } });
  const resource = resources?.find((r) => r.id === id);
  if (!resource) notFound();
  const path = { resource_id: id };
  const [{ data: schedule }, { data: exceptions }, { data: free }, { data: calendar }] = await Promise.all([
    api.GET("/api/crm/resources/{resource_id}/schedule", { params: { header, path } }),
    api.GET("/api/crm/resources/{resource_id}/exceptions", { params: { header, path } }),
    api.GET("/api/crm/resources/{resource_id}/free", { params: { header, path, query: { start: isoDate(new Date()), days: 7 } } }),
    api.GET("/api/crm/resources/{resource_id}/calendar", { params: { header, path } }),
  ]);

  const hours: Partial<Record<Day, { start: string; end: string }[]>> = {};
  for (const r of schedule?.rules ?? []) {
    const day = DAYS[r.weekday - 1];
    (hours[day] ??= []).push({ start: r.start, end: r.end });
  }
  const tz = free?.timezone;
  const when = (iso: string, opts: DateTimeFormatOptions) => format.dateTime(new Date(iso), { ...opts, timeZone: tz });

  return (
    <>
      <h2 className="text-xl font-semibold" data-testid="resource-name">
        {resource.name}
      </h2>
      <Notice params={await searchParams} />
      <Card>
        <CardHeader>
          <CardTitle>{t("team.schedule")}</CardTitle>
        </CardHeader>
        <CardContent>
          <form action={saveSchedule} className="flex flex-col gap-4">
            <input type="hidden" name="tenant_id" value={tenantId} />
            <input type="hidden" name="id" value={id} />
            <HoursEditor hours={hours} prefix="schedule" />
            <Button type="submit" className="self-start" data-testid="save-schedule">
              {tc("save")}
            </Button>
          </form>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>{t("team.daysOff")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {(exceptions ?? []).length > 0 && (
            <ul className="divide-y text-sm">
              {(exceptions ?? []).map((e) => (
                <li key={e.id} className="flex flex-wrap items-center gap-3 py-2" data-day-off={e.reason ?? ""}>
                  <span className="flex-1">
                    {when(e.start, { dateStyle: "medium" })} – {when(new Date(new Date(e.end).getTime() - 1).toISOString(), { dateStyle: "medium" })}
                    {e.reason && <span className="text-muted-foreground"> · {e.reason}</span>}
                  </span>
                  <form action={deleteDayOff}>
                    <input type="hidden" name="tenant_id" value={tenantId} />
                    <input type="hidden" name="id" value={id} />
                    <input type="hidden" name="exception_id" value={e.id} />
                    <Button type="submit" size="sm" variant="ghost">
                      {t("delete")}
                    </Button>
                  </form>
                </li>
              ))}
            </ul>
          )}
          <form action={addDayOff} className="grid gap-3 sm:grid-cols-4 sm:items-end">
            <input type="hidden" name="tenant_id" value={tenantId} />
            <input type="hidden" name="id" value={id} />
            <div className="grid gap-1.5">
              <Label htmlFor="off-first">{t("team.firstDay")}</Label>
              <Input id="off-first" name="first_day" type="date" required />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="off-last">{t("team.lastDay")}</Label>
              <Input id="off-last" name="last_day" type="date" />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="off-reason">{t("team.reason")}</Label>
              <Input id="off-reason" name="reason" />
            </div>
            <Button type="submit" data-testid="add-day-off">
              {t("team.addDayOff")}
            </Button>
          </form>
        </CardContent>
      </Card>
      <Card data-testid="calendar-card">
        <CardHeader>
          <CardTitle>{tcal("title")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <p className="text-muted-foreground">{tcal("intro")}</p>
          {calendar && (
            <div className="space-y-1" data-calendar-status={calendar.status}>
              <p className="font-medium">{tcal("connectedAs", { email: calendar.account_email ?? "" })}</p>
              {calendar.status === "revoked" ? (
                <p className="text-destructive">{tcal("revoked")}</p>
              ) : (
                <>
                  <p className="text-muted-foreground">
                    {calendar.last_synced_at
                      ? tcal("lastSynced", { when: format.relativeTime(new Date(calendar.last_synced_at)) })
                      : tcal("neverSynced")}
                    {" · "}
                    {calendar.live_updates ? tcal("live") : tcal("polling")}
                  </p>
                  {calendar.status === "error" && calendar.last_error && (
                    <p className="text-destructive">{tcal("error", { error: calendar.last_error })}</p>
                  )}
                </>
              )}
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            {(!calendar || calendar.status === "revoked") && (
              <form action={connectOutlook}>
                <input type="hidden" name="tenant_id" value={tenantId} />
                <input type="hidden" name="id" value={id} />
                <Button type="submit" data-testid="connect-outlook">
                  {calendar ? tcal("reconnect") : tcal("connect")}
                </Button>
              </form>
            )}
            {calendar && calendar.status !== "revoked" && (
              <form action={syncCalendar}>
                <input type="hidden" name="tenant_id" value={tenantId} />
                <input type="hidden" name="id" value={id} />
                <Button type="submit" variant="outline">
                  {tcal("syncNow")}
                </Button>
              </form>
            )}
            {calendar && (
              <form action={disconnectCalendar}>
                <input type="hidden" name="tenant_id" value={tenantId} />
                <input type="hidden" name="id" value={id} />
                <Button type="submit" variant="ghost">
                  {tcal("disconnect")}
                </Button>
              </form>
            )}
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>{t("team.freeTime")}</CardTitle>
        </CardHeader>
        <CardContent>
          {(free?.spans ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("team.noFreeTime")}</p>
          ) : (
            <ul className="grid gap-1 text-sm" data-testid="free-time">
              {(free?.spans ?? []).map((s) => (
                <li key={s.start} data-span={`${s.start.slice(0, 16)}/${s.end.slice(11, 16)}`}>
                  {when(s.start, { weekday: "long", day: "numeric", month: "short" })}: {when(s.start, { timeStyle: "short", hourCycle: "h23" })} – {when(s.end, { timeStyle: "short", hourCycle: "h23" })}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </>
  );
}
