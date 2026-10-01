"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { apiClient, inTenant } from "@/lib/api/client";

export async function retryJob(form: FormData): Promise<void> {
  const { error } = await (await apiClient()).POST("/api/system/jobs/{job_id}/retry", {
    params: { header: inTenant(String(form.get("tenant_id"))), path: { job_id: Number(form.get("job_id")) } },
  });
  revalidatePath("/settings/system");
  redirect(error ? "/settings/system?error=1" : "/settings/system?retried=1");
}
