import { notFound, redirect } from "next/navigation";

import { currentTenantId, getManifest, getMe, type Manifest, type Me } from "@/lib/api/client";

export type Session = { me: Me; tenantId: string; manifest: Manifest };

// For pages inside a business: the signed-in user, current tenant and manifest.
// Sends users without a business to the home page (which offers to create one).
export async function requireTenant(): Promise<Session> {
  const me = await getMe();
  const tenantId = me ? await currentTenantId(me) : null;
  if (!me || !tenantId) redirect("/");
  return { me, tenantId, manifest: await getManifest(tenantId) };
}

// For a module's pages: 404 when the business has the module switched off,
// matching the API.
export async function requireModule(key: string): Promise<Session> {
  const session = await requireTenant();
  if (!session.manifest.modules.includes(key)) notFound();
  return session;
}
