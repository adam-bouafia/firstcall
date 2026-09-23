// Same-origin proxy: the browser talks to /fc/*, the Next.js server forwards to the API.
// FIRSTCALL_API_URL is read at request time, so one image works locally, in Docker and in-cluster.
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";
const upstream = () => (process.env.FIRSTCALL_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

async function forward(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  const url = `${upstream()}/${path.join("/")}${req.nextUrl.search}`;
  const init: RequestInit = { method: req.method, headers: { "content-type": req.headers.get("content-type") ?? "application/json" }, cache: "no-store" };
  if (req.method !== "GET" && req.method !== "HEAD") init.body = await req.text();
  try {
    const r = await fetch(url, init);
    return new Response(r.body, { status: r.status, headers: { "content-type": r.headers.get("content-type") ?? "application/json" } });
  } catch (e) {
    return Response.json({ detail: `FirstCall API unreachable at ${upstream()}: ${e}` }, { status: 502 });
  }
}

export { forward as GET, forward as POST };
