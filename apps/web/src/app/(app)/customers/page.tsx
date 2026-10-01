import { ComingSoon } from "@/components/coming-soon";
import { requireModule } from "@/lib/session";

export default async function Page() {
  await requireModule("crm");
  return <ComingSoon title="Customers" card="Customer profile page" />;
}
