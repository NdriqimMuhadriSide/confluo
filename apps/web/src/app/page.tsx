import { connection } from "next/server";

import { signOut } from "@/app/(auth)/actions";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { API_URL, getHealth, getMe } from "@/lib/api/client";

// Placeholder home page: proves web → API → database and sign-in are wired up.
// The real dashboard shell comes with its own card.
export default async function Home() {
  await connection();
  const [health, me] = await Promise.all([getHealth(), getMe()]);

  const rows: [string, boolean][] = [
    ["API", health !== null],
    ["Database", health?.database ?? false],
    ["API accepts your session", me !== null],
  ];

  return (
    <main className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center gap-6 p-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Confluo</h1>
        <form action={signOut}>
          <Button type="submit" variant="outline" size="sm">
            Sign out
          </Button>
        </form>
      </div>
      <p className="text-sm text-muted-foreground">
        Signed in as <span className="font-medium text-foreground">{me?.email ?? "…"}</span>
      </p>
      <Card>
        <CardHeader>
          <CardTitle>Local stack</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {rows.map(([name, ok]) => (
            <div key={name} className="flex items-center justify-between">
              <span>{name}</span>
              <span className={ok ? "text-emerald-600" : "text-red-600"}>{ok ? "up" : "down"}</span>
            </div>
          ))}
          <p className="pt-2 text-xs text-muted-foreground">{API_URL}</p>
        </CardContent>
      </Card>
    </main>
  );
}
