import { getTranslations } from "next-intl/server";

import { Input } from "@/components/ui/input";

export const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
export type Day = (typeof DAYS)[number];
type Iv = { start: string; end: string };

const hhmm = (t: string) => t.slice(0, 5);

// One row per weekday: closed, from-until and an optional break. Field names are
// read back by `weekly()` in setup-actions.ts. `prefix` keeps ids unique per form.
export async function HoursEditor({ hours, prefix }: { hours: Partial<Record<Day, Iv[]>>; prefix: string }) {
  const t = await getTranslations("setup");
  const grid = "grid grid-cols-2 items-center gap-2 sm:grid-cols-[8rem_auto_1fr_1fr_1fr_1fr]";
  return (
    <div className="grid gap-2">
      <div className={`${grid} hidden text-xs text-muted-foreground sm:grid`} aria-hidden>
        <span />
        <span />
        <span>{t("from")}</span>
        <span>{t("until")}</span>
        <span>{t("breakFrom")}</span>
        <span>{t("breakUntil")}</span>
      </div>
      {DAYS.map((day) => {
        const iv = hours[day] ?? [];
        const first = iv[0];
        const last = iv[iv.length - 1];
        const hasBreak = iv.length >= 2;
        const id = (s: string) => `${prefix}-${day}-${s}`;
        return (
          <fieldset key={day} className={grid} data-day={day}>
            <legend className="sr-only">{t(`weekdays.${day}`)}</legend>
            <span className="text-sm font-medium">{t(`weekdays.${day}`)}</span>
            <label className="flex items-center gap-1 text-sm">
              <input type="checkbox" name={`${day}_closed`} id={id("closed")} defaultChecked={iv.length === 0} />
              {t("closed")}
            </label>
            <Input aria-label={`${t(`weekdays.${day}`)} ${t("from")}`} type="time" name={`${day}_from`} defaultValue={first ? hhmm(first.start) : "09:00"} />
            <Input aria-label={`${t(`weekdays.${day}`)} ${t("until")}`} type="time" name={`${day}_until`} defaultValue={last ? hhmm(last.end) : "17:00"} />
            <span className="col-span-2 text-xs text-muted-foreground sm:hidden">
              {t("breakFrom")} – {t("breakUntil")}
            </span>
            <Input aria-label={`${t(`weekdays.${day}`)} ${t("breakFrom")}`} type="time" name={`${day}_break_from`} defaultValue={hasBreak ? hhmm(iv[0].end) : ""} />
            <Input aria-label={`${t(`weekdays.${day}`)} ${t("breakUntil")}`} type="time" name={`${day}_break_until`} defaultValue={hasBreak ? hhmm(iv[1].start) : ""} />
          </fieldset>
        );
      })}
    </div>
  );
}
