import type { Metadata } from "next";
import { getLocale, getTranslations } from "next-intl/server";

import { Notice } from "@/components/notice";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiClient, inTenant, type Schemas } from "@/lib/api/client";
import { LOCALES, LOCALE_NAMES } from "@/i18n/config";
import { requireModule } from "@/lib/session";
import { cn } from "@/lib/utils";

import { deleteItem, saveItem, setPublished } from "./actions";

export const metadata: Metadata = { title: "Knowledge base · Confluo" };

const KINDS = ["faq", "service", "price", "hours", "location", "policy", "free_text"] as const;
const STATUS_STYLE = { draft: "bg-muted text-muted-foreground", indexing: "bg-amber-100 text-amber-900", live: "bg-emerald-100 text-emerald-900" };

async function ItemForm({ tenantId, item }: { tenantId: string; item?: Schemas["ItemOut"] }) {
  const t = await getTranslations("kb");
  const tc = await getTranslations("common");
  const locale = await getLocale();
  const p = item?.id ?? "new";
  return (
    <form action={saveItem} className="grid gap-3" data-kb-form={item?.title ?? "new"}>
      <input type="hidden" name="tenant_id" value={tenantId} />
      {item && <input type="hidden" name="id" value={item.id} />}
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="grid gap-1.5">
          <Label htmlFor={`${p}-kind`}>{t("kind")}</Label>
          <select id={`${p}-kind`} name="kind" defaultValue={item?.kind ?? "faq"} className="h-9 rounded-md border bg-background px-2 text-sm">
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {t(`kinds.${k}`)}
              </option>
            ))}
          </select>
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor={`${p}-lang`}>{t("language")}</Label>
          <select id={`${p}-lang`} name="language" defaultValue={item?.language ?? locale} className="h-9 rounded-md border bg-background px-2 text-sm">
            {LOCALES.map((l) => (
              <option key={l} value={l}>
                {LOCALE_NAMES[l]}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor={`${p}-title`}>{t("itemTitle")}</Label>
        <Input id={`${p}-title`} name="title" defaultValue={item?.title} required maxLength={200} />
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor={`${p}-body`}>{t("body")}</Label>
        <textarea
          id={`${p}-body`}
          name="body"
          defaultValue={item?.body}
          required
          rows={item ? 6 : 4}
          maxLength={20000}
          className="rounded-md border bg-background px-3 py-2 text-sm"
        />
      </div>
      <Button type="submit" className="self-start" data-testid={item ? "save-kb" : "create-kb"}>
        {item ? tc("save") : t("create")}
      </Button>
    </form>
  );
}

export default async function KnowledgePage({ searchParams }: PageProps<"/knowledge">) {
  const t = await getTranslations("kb");
  const ts = await getTranslations("setup");
  const { tenantId, manifest } = await requireModule("crm");
  const params = await searchParams;
  const q = typeof params.q === "string" ? params.q.trim() : "";
  const api = await apiClient();
  const header = inTenant(tenantId);
  const canEdit = manifest.permissions.includes("crm.kb.edit");
  const [{ data: items }, search] = await Promise.all([
    canEdit ? api.GET("/api/crm/knowledge", { params: { header } }) : Promise.resolve({ data: [] as Schemas["ItemOut"][] }),
    q.length >= 2 ? api.GET("/api/crm/knowledge/search", { params: { header, query: { q, limit: 5 } } }) : Promise.resolve(null),
  ]);

  return (
    <>
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <p className="text-sm text-muted-foreground">{t("intro")}</p>
      </div>
      <Notice params={params} />

      <Card>
        <CardHeader>
          <CardTitle>{t("try")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <form className="flex gap-2" role="search">
            <Input type="search" name="q" defaultValue={q} placeholder="…" aria-label={t("try")} className="flex-1" />
            <Button type="submit" variant="outline" data-testid="kb-search">
              {t("search")}
            </Button>
          </form>
          <p className="text-xs text-muted-foreground">{t("tryHint")}</p>
          {search &&
            (search.data && search.data.length > 0 ? (
              <ol className="space-y-2 text-sm" data-testid="kb-hits">
                {search.data.map((h, i) => (
                  <li key={`${h.item_id}-${i}`} className="rounded-md border p-2" data-hit={h.title}>
                    <p className="font-medium">
                      {h.title} <span className="text-xs font-normal text-muted-foreground">· {t("score", { score: h.score.toFixed(4) })}</span>
                    </p>
                    <p className="line-clamp-3 whitespace-pre-line text-muted-foreground">{h.content.split("\n").slice(1).join("\n")}</p>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="text-sm text-muted-foreground">{t("noHits")}</p>
            ))}
        </CardContent>
      </Card>

      {canEdit &&
        (items ?? []).map((item) => (
          <Card key={item.id} data-kb-item={item.title}>
            <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
              <CardTitle className="flex flex-wrap items-center gap-2">
                {item.title}
                <span className="text-xs font-normal text-muted-foreground">
                  {t(`kinds.${item.kind}`)} · {item.language.toUpperCase()}
                </span>
                <span className={cn("rounded px-1.5 py-0.5 text-xs font-medium", STATUS_STYLE[item.status])} data-status={item.status}>
                  {t(`status.${item.status}`)}
                  {item.status === "live" && ` · ${t("chunks", { count: item.chunks })}`}
                </span>
              </CardTitle>
              <div className="flex gap-1">
                <form action={setPublished}>
                  <input type="hidden" name="tenant_id" value={tenantId} />
                  <input type="hidden" name="id" value={item.id} />
                  <input type="hidden" name="publish" value={String(!item.published)} />
                  <Button type="submit" size="sm" variant={item.published ? "outline" : "default"} data-testid={item.published ? "unpublish" : "publish"}>
                    {item.published ? t("unpublish") : t("publish")}
                  </Button>
                </form>
                <form action={deleteItem}>
                  <input type="hidden" name="tenant_id" value={tenantId} />
                  <input type="hidden" name="id" value={item.id} />
                  <Button type="submit" size="sm" variant="ghost">
                    {ts("delete")}
                  </Button>
                </form>
              </div>
            </CardHeader>
            <CardContent>
              <details>
                <summary className="cursor-pointer text-sm text-muted-foreground">{ts("edit")}</summary>
                <div className="mt-3">
                  <ItemForm tenantId={tenantId} item={item} />
                </div>
              </details>
            </CardContent>
          </Card>
        ))}

      {canEdit && (
        <Card>
          <CardHeader>
            <CardTitle>{t("new")}</CardTitle>
          </CardHeader>
          <CardContent>
            <ItemForm tenantId={tenantId} />
          </CardContent>
        </Card>
      )}
    </>
  );
}
