export const API = process.env.NEXT_PUBLIC_API ?? "http://localhost:8000";
const tok = () => (typeof window === "undefined" ? "" : localStorage.getItem("token") ?? "");
export async function api<T = any>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(API + path, { ...init, headers: { "Content-Type": "application/json", Authorization: `Bearer ${tok()}`, ...(init.headers ?? {}) } });
  if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 200)}`);
  return r.json();
}
export type SseEvent = { event: string; data: any };
/** POST/GET an SSE endpoint with auth header (EventSource cannot set headers). */
export async function sse(path: string, onEvent: (e: SseEvent) => void, method = "POST") {
  const r = await fetch(API + path, { method, headers: { Authorization: `Bearer ${tok()}` } });
  const reader = r.body!.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read(); if (done) break;
    buf += value; const parts = buf.split("\n\n"); buf = parts.pop()!;
    for (const p of parts) {
      const ev = /event: (.*)/.exec(p)?.[1] ?? "message", d = /data: (.*)/.exec(p)?.[1] ?? "null";
      onEvent({ event: ev, data: JSON.parse(d) });
    }
  }
}
