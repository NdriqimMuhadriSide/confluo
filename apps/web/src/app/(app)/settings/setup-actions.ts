"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { apiClient, inTenant, type Schemas } from "@/lib/api/client";

const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
const LANGS = ["en", "nl", "fr", "de", "sq"] as const;

type Detail = { loc?: (string | number)[]; msg?: string }[];

function describe(error: unknown): string {
  const detail = (error as { detail?: unknown } | undefined)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return (detail as Detail).map((d) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg}`).join("; ");
  }
  return "Could not save.";
}

// Back to the page with a notice; the pages show ?saved / ?error.
function done(path: string, error?: unknown): never {
  revalidatePath(path);
  redirect(error ? `${path}?error=${encodeURIComponent(describe(error))}` : `${path}?saved=1`);
}

const str = (form: FormData, key: string) => String(form.get(key) ?? "").trim();
const num = (form: FormData, key: string) => Number(form.get(key) || 0);

// Weekly hours from the HoursEditor inputs: <day>_from/_until, optional break.
function weekly(form: FormData) {
  return DAYS.map((day) => {
    if (form.get(`${day}_closed`) === "on") return [] as { start: string; end: string }[];
    const from = str(form, `${day}_from`);
    const until = str(form, `${day}_until`);
    if (!from || !until) return [];
    const bFrom = str(form, `${day}_break_from`);
    const bUntil = str(form, `${day}_break_until`);
    if (bFrom && bUntil) {
      return [
        { start: from, end: bFrom },
        { start: bUntil, end: until },
      ];
    }
    return [{ start: from, end: until }];
  });
}

// --- Locations -------------------------------------------------------------------------

export async function saveLocation(form: FormData): Promise<void> {
  const tenant = str(form, "tenant_id");
  const id = str(form, "id");
  const [mon, tue, wed, thu, fri, sat, sun] = weekly(form);
  const body = {
    name: str(form, "name"),
    address: str(form, "address") || null,
    timezone: str(form, "timezone"),
    opening_hours: { mon, tue, wed, thu, fri, sat, sun },
  };
  const api = await apiClient();
  const { error } = id
    ? await api.PATCH("/api/locations/{location_id}", { params: { header: inTenant(tenant), path: { location_id: id } }, body })
    : await api.POST("/api/locations", { params: { header: inTenant(tenant) }, body });
  done("/settings/locations", error);
}

export async function deleteLocation(form: FormData): Promise<void> {
  const { error } = await (await apiClient()).DELETE("/api/locations/{location_id}", {
    params: { header: inTenant(str(form, "tenant_id")), path: { location_id: str(form, "id") } },
  });
  done("/settings/locations", error);
}

// --- Booking fields ---------------------------------------------------------------------

function keyFrom(label: string): string {
  const key = label
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return /^[a-z]/.test(key) ? key.slice(0, 60) : `field_${key}`.slice(0, 60);
}

export async function createField(form: FormData): Promise<void> {
  const label = str(form, "label");
  const type = str(form, "type") as Schemas["FieldIn"]["type"];
  const options = type === "select" ? str(form, "options").split(",").map((o) => o.trim()).filter(Boolean) : [];
  const { error } = await (await apiClient()).POST("/api/fields", {
    params: { header: inTenant(str(form, "tenant_id")) },
    body: {
      entity: str(form, "entity") as Schemas["FieldIn"]["entity"],
      key: keyFrom(label),
      label_i18n: { [str(form, "lang")]: label },
      type,
      options,
      pii_level: str(form, "pii_level") as Schemas["FieldIn"]["pii_level"],
      position: 0,
    },
  });
  done("/settings/fields", error);
}

export async function archiveField(form: FormData): Promise<void> {
  const { error } = await (await apiClient()).DELETE("/api/fields/{field_id}", {
    params: { header: inTenant(str(form, "tenant_id")), path: { field_id: str(form, "id") } },
  });
  done("/settings/fields", error);
}

// --- Services -----------------------------------------------------------------------------

export async function saveService(form: FormData): Promise<void> {
  const tenant = str(form, "tenant_id");
  const id = str(form, "id");
  const names: Record<string, string> = Object.fromEntries(
    LANGS.map((l) => [l, str(form, `name_${l}`)]).filter(([, v]) => v),
  );
  const fieldIds = form.getAll("field").map(String);
  const body = {
    name_i18n: names,
    description_i18n: {},
    position: 0,
    duration_min: num(form, "duration_min"),
    buffer_before_min: num(form, "buffer_before_min"),
    buffer_after_min: num(form, "buffer_after_min"),
    price_cents: form.get("price") ? Math.round(Number(form.get("price")) * 100) : null,
    active: form.get("active") === "on",
    fields: fieldIds.map((f) => ({ field_id: f, required: form.get(`required_${f}`) === "on" })),
    resource_ids: form.getAll("resource").map(String),
  };
  const api = await apiClient();
  const { error } = id
    ? await api.PUT("/api/crm/services/{service_id}", { params: { header: inTenant(tenant), path: { service_id: id } }, body })
    : await api.POST("/api/crm/services", { params: { header: inTenant(tenant) }, body });
  done("/settings/services", error);
}

export async function deleteService(form: FormData): Promise<void> {
  const { error } = await (await apiClient()).DELETE("/api/crm/services/{service_id}", {
    params: { header: inTenant(str(form, "tenant_id")), path: { service_id: str(form, "id") } },
  });
  done("/settings/services", error);
}

// --- Staff and rooms ----------------------------------------------------------------------

export async function createResource(form: FormData): Promise<void> {
  const { error } = await (await apiClient()).POST("/api/crm/resources", {
    params: { header: inTenant(str(form, "tenant_id")) },
    body: {
      kind: str(form, "kind") as Schemas["ResourceIn"]["kind"],
      name: str(form, "name"),
      location_id: str(form, "location_id") || null,
      member_id: str(form, "member_id") || null,
      active: true,
    },
  });
  done("/settings/team", error);
}

export async function saveSchedule(form: FormData): Promise<void> {
  const id = str(form, "id");
  const rules = weekly(form).flatMap((intervals, i) => intervals.map((iv) => ({ weekday: i + 1, ...iv })));
  const { error } = await (await apiClient()).PUT("/api/crm/resources/{resource_id}/schedule", {
    params: { header: inTenant(str(form, "tenant_id")), path: { resource_id: id } },
    body: { rules },
  });
  done(`/settings/team/${id}`, error);
}

export async function addDayOff(form: FormData): Promise<void> {
  const id = str(form, "id");
  const { error } = await (await apiClient()).POST("/api/crm/resources/{resource_id}/exceptions", {
    params: { header: inTenant(str(form, "tenant_id")), path: { resource_id: id } },
    body: {
      kind: "off",
      first_day: str(form, "first_day"),
      last_day: str(form, "last_day") || null,
      reason: str(form, "reason") || null,
    },
  });
  done(`/settings/team/${id}`, error);
}

export async function deleteDayOff(form: FormData): Promise<void> {
  const id = str(form, "id");
  const { error } = await (await apiClient()).DELETE("/api/crm/resources/{resource_id}/exceptions/{exception_id}", {
    params: { header: inTenant(str(form, "tenant_id")), path: { resource_id: id, exception_id: str(form, "exception_id") } },
  });
  done(`/settings/team/${id}`, error);
}

// --- External calendar (Outlook) ---------------------------------------------------------

export async function connectOutlook(form: FormData): Promise<void> {
  const id = str(form, "id");
  const { data, error } = await (await apiClient()).GET("/api/crm/calendar/microsoft/authorize", {
    params: { header: inTenant(str(form, "tenant_id")), query: { resource_id: id } },
  });
  if (!data) done(`/settings/team/${id}`, error);
  redirect(data.url);
}

export async function syncCalendar(form: FormData): Promise<void> {
  const id = str(form, "id");
  const { error } = await (await apiClient()).POST("/api/crm/resources/{resource_id}/calendar/sync", {
    params: { header: inTenant(str(form, "tenant_id")), path: { resource_id: id } },
  });
  done(`/settings/team/${id}`, error);
}

export async function disconnectCalendar(form: FormData): Promise<void> {
  const id = str(form, "id");
  const { error } = await (await apiClient()).DELETE("/api/crm/resources/{resource_id}/calendar", {
    params: { header: inTenant(str(form, "tenant_id")), path: { resource_id: id } },
  });
  done(`/settings/team/${id}`, error);
}

// --- Channels --------------------------------------------------------------------------------

export async function saveWhatsApp(form: FormData): Promise<void> {
  const token = str(form, "access_token");
  const { error } = await (await apiClient()).PUT("/api/crm/channels/whatsapp", {
    params: { header: inTenant(str(form, "tenant_id")) },
    body: {
      enabled: form.get("enabled") === "on",
      phone_number_id: str(form, "phone_number_id"),
      display_phone: str(form, "display_phone") || null,
      access_token: token || null,
    },
  });
  done("/settings/channels", error);
}
