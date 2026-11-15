"use client";
import { useEffect, useState, useRef } from "react";
import Link from "next/link";
import { API } from "@/lib/api";

type NodeEvent = { node?: string; status?: string; tokens?: number; trace_id?: string };
type SseEvent = { event: string; data: NodeEvent };

const STATUS_LABELS: Record<string, string> = {
  connecting: "Connecting…",
  running: "Running",
  done: "Done",
  "awaiting approval": "Awaiting Approval",
  error: "Error",
  "connection lost": "Connection Lost",
};

const STATUS_COLORS: Record<string, string> = {
  connecting: "text-gray-500",
  running: "text-blue-600",
  done: "text-emerald-600",
  "awaiting approval": "text-amber-600",
  error: "text-red-600",
  "connection lost": "text-red-500",
};

export default function Run({ params }: { params: { id: string } }) {
  const [events, setEvents] = useState<SseEvent[]>([]);
  const [state, setState] = useState<string>("connecting");
  const [err, setErr] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const token = typeof window !== "undefined" ? localStorage.getItem("token") ?? "" : "";
    if (!token) { setState("connection lost"); setErr("Not logged in"); return; }

    setState("running");
    let aborted = false;

    async function stream() {
      try {
        const r = await fetch(`${API}/runs/${params.id}`, {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!r.ok) { throw new Error(`${r.status}: ${await r.text()}`); }
        const reader = r.body!.pipeThrough(new TextDecoderStream()).getReader();
        let buf = "";
        while (!aborted) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += value;
          const parts = buf.split("\n\n");
          buf = parts.pop() ?? "";
          for (const p of parts) {
            if (!p.trim()) continue;
            const eventMatch = /event: (.+)/.exec(p);
            const dataMatch = /data: (.+)/.exec(p);
            const ev = eventMatch?.[1]?.trim() ?? "message";
            let data: NodeEvent = {};
            try { data = JSON.parse(dataMatch?.[1] ?? "{}"); } catch { /* ignore parse errors */ }
            const se: SseEvent = { event: ev, data };
            setEvents(x => [...x, se]);
            if (ev === "interrupt") { setState("awaiting approval"); }
            else if (ev === "done") { setState("done"); return; }
            else if (ev === "error") { setState("error"); setErr(String(data)); return; }
          }
        }
      } catch (e: any) {
        if (!aborted) { setState("connection lost"); setErr(e.message ?? String(e)); }
      }
    }

    stream();
    return () => { aborted = true; };
  }, [params.id]);

  // Auto-scroll to bottom as events arrive
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [events]);

  const statusColor = STATUS_COLORS[state] ?? "text-gray-500";

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-bold">Agent Run — {params.id}</h1>
        <Link href={`/job/${params.id}`} className="text-sm underline text-slate-600">← Back to job</Link>
      </div>

      <div className={`flex items-center gap-2 font-medium ${statusColor}`}>
        {(state === "running" || state === "connecting") && (
          <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
        )}
        <span data-testid="run-state">{STATUS_LABELS[state] ?? state}</span>
      </div>

      {err && <p role="alert" className="rounded bg-red-50 border border-red-200 p-2 text-sm text-red-700">{err}</p>}

      {state === "awaiting approval" && (
        <div className="rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-800">
          The agent is waiting for your approval.{" "}
          <Link href={`/studio/${params.id}`} className="font-medium underline">Open Proposal Studio →</Link>
        </div>
      )}

      {events.length === 0 && state === "running" && (
        <p className="text-sm text-slate-500">Waiting for the agent to start…</p>
      )}

      <ol className="space-y-1">
        {events.map((e, i) => (
          <li
            key={i}
            className={`rounded border p-2 text-sm ${
              e.event === "error" ? "border-red-200 bg-red-50" :
              e.event === "interrupt" ? "border-amber-200 bg-amber-50" :
              e.event === "done" ? "border-emerald-200 bg-emerald-50" :
              "border-slate-100 bg-white"
            }`}
          >
            <span className="font-mono text-xs text-slate-500 mr-2">[{e.event}]</span>
            <b>{e.data?.node ?? e.event}</b>
            {e.data?.status && <span className="ml-2 text-slate-600">{e.data.status}</span>}
            {e.data?.tokens ? <span className="ml-2 text-slate-400 text-xs">· {e.data.tokens} tok</span> : null}
            {e.data?.trace_id && (
              <span className="ml-2 text-xs text-slate-400">trace: {e.data.trace_id}</span>
            )}
          </li>
        ))}
      </ol>
      <div ref={bottomRef} />
    </div>
  );
}
