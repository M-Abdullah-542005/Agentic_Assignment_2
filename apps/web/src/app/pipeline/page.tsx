"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

const STAGES = ["drafted", "submitted", "viewed", "replied", "interview", "hired", "lost"];

const STAGE_COLORS: Record<string, string> = {
  drafted:   "bg-slate-100 text-slate-700",
  submitted: "bg-blue-100 text-blue-700",
  viewed:    "bg-indigo-100 text-indigo-700",
  replied:   "bg-purple-100 text-purple-700",
  interview: "bg-amber-100 text-amber-700",
  hired:     "bg-emerald-100 text-emerald-700",
  lost:      "bg-red-100 text-red-700",
};

export default function Pipeline() {
  const [rows, setRows]       = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr]         = useState("");

  useEffect(() => {
    const token = typeof window !== "undefined" ? localStorage.getItem("token") : "";
    if (!token) { setErr("Not logged in"); setLoading(false); return; }
    const load = () =>
      api<any[]>("/jobs")
        .then(r => { setRows(r); setLoading(false); })
        .catch(e => { setErr(e.message); setLoading(false); });
    load();
    const id = setInterval(load, 8000);
    return () => clearInterval(id);
  }, []);

  const stage = (r: any): string => {
    if (r.outcome_stage && STAGES.includes(r.outcome_stage)) return r.outcome_stage;
    if (r.status === "submitted") return "submitted";
    if (r.status === "rejected" || r.status === "skipped") return "lost";
    return "drafted";
  };

  if (err) return <p role="alert" className="p-4 text-red-600">{err}</p>;

  return (
    <div className="space-y-4 p-4">
      <h1 className="text-xl font-bold text-slate-800">Pipeline Board</h1>
      <p className="text-sm text-slate-500">Kanban view — {rows.length} total jobs tracked</p>

      {loading && <p className="text-slate-400 text-sm">Loading…</p>}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-7 overflow-x-auto">
        {STAGES.map(col => {
          const cards = rows.filter(r => stage(r) === col);
          return (
            <section key={col} className="rounded-lg bg-slate-50 border p-2 min-w-[120px]">
              <div className="flex items-center justify-between mb-2">
                <h2 className={`text-xs font-bold uppercase rounded px-1.5 py-0.5 ${STAGE_COLORS[col]}`}>
                  {col}
                </h2>
                <span className="text-xs text-slate-400">{cards.length}</span>
              </div>
              <ul className="space-y-1">
                {cards.map(r => (
                  <li key={r.job_id}>
                    <Link
                      href={`/job/${r.job_id}`}
                      className="block rounded bg-white border p-2 text-xs hover:border-blue-300 hover:shadow-sm transition"
                    >
                      <span className="line-clamp-2 font-medium text-slate-700">{r.title}</span>
                      {r.price && (
                        <span className="mt-1 block text-slate-400">${r.price}</span>
                      )}
                    </Link>
                  </li>
                ))}
                {cards.length === 0 && (
                  <li className="text-xs text-slate-300 italic text-center py-2">empty</li>
                )}
              </ul>
            </section>
          );
        })}
      </div>
    </div>
  );
}
