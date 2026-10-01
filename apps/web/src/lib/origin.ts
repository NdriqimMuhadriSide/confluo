import type { NextRequest } from "next/server";

// The origin the browser used. `request.nextUrl.origin` is the server's bind address
// when Next runs standalone in a container (http://0.0.0.0:3000), so derive it from
// the Host / X-Forwarded-* headers instead.
export function originFromHeaders(headers: Headers): string {
  const host = headers.get("x-forwarded-host") ?? headers.get("host") ?? "localhost:3000";
  const proto = headers.get("x-forwarded-proto") ?? "http";
  return `${proto}://${host}`;
}

export function redirectUrl(request: NextRequest, path: string): URL {
  return new URL(path, originFromHeaders(request.headers));
}
