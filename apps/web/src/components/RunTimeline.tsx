"use client";
import { useEffect, useRef, useState } from "react";
import { API } from "@/lib/api";

type NodeEvent = { event: string; data: Record<string, any>; ts: number };

const NODE_LABELS: Record<string, string> = {
  scout:        "🔍 Scout",
  analyst:      "📊 Analyst",
  risk:         "⚠️ Risk Assessor",
  fit_matcher:  "🎯 Fit Matcher",
  decision:     "🧠 Decision",
  pricing:      "💰 Pricing",
  writer:       "✍️ Writer",
  verifier:     "✅ Verifier",
  critic:       "🔎 Critic",
  compliance:   "📋 Compliance",
  approval:     "⏸ Approval Gate",
};

const EVENT_COLOR: Record<string, string> = {
  queued:    "bg-blue-50  border-blue-200  text-blue-700",
  node:      "bg-white    border-slate-200 text-slate-700",
  interrupt: "bg-amber-50 border-amber-300 text-amber-800",
  done:      "bg-emerald-50 border-emerald-300 text-emerald-800",
  error:     "bg-red-50   border-red-300   text-red-700",
};

type Props = {
  jobId: string;
  /** If true, attach to existing SSE stream instead of starting a new run */
  resume?: boolean;
  onDone?: (status: string) => void;
  onInterrupt?: () => void;
};

export default function RunTimeline({ jobId, resume = false, onDone, onInterrupt }: Props) {
  const [events, setEvents] = useState<NodeEvent[]>([]);
  const [status, setStatus] = useState("connecting");
  const [err, setErr]       = useState("");
  const aborted = useRef(false);
  const bottom  = useRef<HTMLDivElement>(null);

  useEffect(() => {
    aborted.current = false;
    setEvents([]); setStatus("connecting"); setErr("");
    const token = typeof window !== "undefined" ? localStorage.getItem("token") ?? "" : "";
    if (!token) { setStatus("error"); setErr("Not logged in"); return; }

    async function stream() {
      const url    = `${API}/runs/${jobId}${resume ? "/resume" : ""}`;
      const method = resume ? "GET" : "POST";
      try {
        const r = await fetch(url, { method, headers: { Authorization: `Bearer ${token}` } });
        if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
        setStatus("running");
        const reader = r.body!.pipeThrough(new TextDecoderStream()).getReader();
        let buf = "";
        while (!aborted.current) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += value;
          const parts = buf.split("\n\n"); buf = parts.pop() ?? "";
          for (const p of parts) {
            if (!p.trim()) continue;
            const ev   = /event:\s*(.+)/.exec(p)?.[1]?.trim() ?? "message";
            let   data: Record<string, any> = {};
            try { data = JSON.parse(/data:\s*(.+)/.exec(p)?.[1] ?? "{}"); } catch {}
            setEvents(x => [...x, { event: ev, data, ts: Date.now() }]);
            if (ev === "interrupt") { setStatus("awaiting approval"); onInterrupt?.(); }
            else if (ev === "done")  { setStatus("done"); onDone?.(data?.status ?? "done"); reader.cancel(); return; }
            else if (ev === "error") { setStatus("error"); setErr(data?.message ?? "run error"); reader.cancel(); return; }
          }
        }
      } catch (e: any) {
        if (!aborted.current) { setStatus("error"); setErr(e.message); }
      }
    }
    stream();
    return () => { aborted.current = true; };
  }, [jobId, resume]);

  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth" }); }, [events]);

  const statusBadge: Record<string, string> = {
    connecting:         "text-slate-400",
    running:            "text-blue-600",
    "awaiting approval":"text-amber-600",
    done:               "text-emerald-600",
    error:              "text-red-600",
  };

  return (
    <div className="space-y-2">
      {/* Status indicator */}
      <div className={`flex items-center gap-2 text-sm font-medium ${statusBadge[status] ?? "text-slate-500"}`}>
        {(status === "running" || status === "connecting") && (
          <span className="h-3 w-3 rounded-full border-2 border-current border-t-transparent animate-spin inline-block" />
        )}
        <span data-testid="timeline-status">
          {{ connecting: "Connecting…", running: "Running", "awaiting approval": "Awaiting Approval ⏸",
             done: "Done ✓", error: "Error ✗" }[status] ?? status}
        </span>
      </div>

      {err && <p className="rounded bg-red-50 border border-red-200 p-2 text-xs text-red-700">{err}</p>}

      {/* Timeline */}
      <ol className="relative border-l border-slate-200 space-y-2 pl-4">
        {events.map((e, i) => {
          const label = e.event === "node"
            ? (NODE_LABELS[e.data?.node ?? ""] ?? e.data?.node ?? "node")
            : e.event;
          return (
            <li key={i} className={`rounded border p-2 text-sm ${EVENT_COLOR[e.event] ?? EVENT_COLOR.node}`}>
              <span className="font-medium">{label}</span>
              {e.data?.status && e.event === "node" && (
                <span className="ml-2 opacity-60">{e.data.status}</span>
              )}
              {e.data?.tokens ? (
                <span className="ml-2 text-xs opacity-50">· {e.data.tokens} tok</span>
              ) : null}
            </li>
          );
        })}
        {events.length === 0 && status !== "error" && (
          <li className="text-xs text-slate-400 italic">Waiting for the first event…</li>
        )}
      </ol>
      <div ref={bottom} />
    </div>
  );
}
