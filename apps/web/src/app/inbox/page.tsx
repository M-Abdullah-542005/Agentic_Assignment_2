"use client";
import Link from "next/link";
import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api";

type Row = {
  job_id: string; title: string; fit: number; risk: number;
  action: string; price: number | null; connects: number; status?: string;
};

export default function Inbox() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [err, setErr] = useState("");
  const [hasToken, setHasToken] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    api<Row[]>("/jobs")
      .then(r => { setRows(r); setLoading(false); })
      .catch(e => {
        if (e.message?.includes("401")) { setHasToken(false); }
        else { setErr(e.message); }
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    const token = typeof window !== "undefined" ? localStorage.getItem("token") : "";
    if (!token) { setHasToken(false); return; }
    load();
    const interval = setInterval(load, 6000);
    return () => clearInterval(interval);
  }, [load]);

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await api("/jobs/refresh", { method: "POST" });
      // Wait a moment then reload the list
      setTimeout(() => { load(); setRefreshing(false); }, 2000);
    } catch (e: any) {
      setErr(e.message);
      setRefreshing(false);
    }
  };

  if (!hasToken) {
    return (
      <div className="rounded border bg-white p-6 text-center space-y-3">
        <h2 className="text-lg font-bold text-slate-800">Not logged in yet</h2>
        <p className="text-sm text-slate-600">Please sign in or create an account to access your job inbox.</p>
        <Link href="/" className="inline-block rounded bg-emerald-600 px-4 py-2 text-white text-sm font-medium">Go to Login</Link>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-slate-800">Job Inbox</h1>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="rounded border px-3 py-1.5 text-sm font-medium hover:bg-slate-50 disabled:opacity-50 flex items-center gap-1"
        >
          {refreshing && <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-slate-500 border-t-transparent" />}
          {refreshing ? "Scouting…" : "🔍 Scout Now"}
        </button>
      </div>

      {err && <p role="alert" className="text-red-600 text-sm bg-red-50 p-2 rounded">{err}</p>}

      {loading && <p className="text-slate-500 text-sm">Loading jobs…</p>}

      {!loading && rows !== null && rows.length === 0 && (
        <div className="rounded border bg-white p-6 text-center space-y-3">
          <p className="text-slate-600">No jobs yet.</p>
          <p className="text-sm text-slate-500">Click "Scout Now" to search for new jobs, or wait for the background scout to run.</p>
        </div>
      )}

      {rows && rows.length > 0 && (
        <ul className="space-y-2">
          {rows.map(r => (
            <li key={r.job_id} className="flex flex-wrap items-center justify-between gap-2 rounded border bg-white p-3 shadow-sm" data-testid="job-card">
              <div className="min-w-0 flex-1">
                <b className="block truncate">{r.title}</b>
                <div className="text-sm text-slate-600 mt-0.5">
                  Fit <span className={`font-medium ${r.fit > 0.6 ? "text-emerald-600" : r.fit > 0.3 ? "text-amber-600" : "text-slate-500"}`}>{((r.fit ?? 0) * 100) | 0}%</span>
                  {" · "}
                  {r.price ? `$${r.price}` : "no quote"}
                  {" · "}
                  {r.connects} Connects
                </div>
              </div>
              <span className={`rounded px-2 py-1 text-xs font-medium ${
                (r.risk ?? 0) > 0.6 ? "bg-red-100 text-red-700" :
                (r.risk ?? 0) > 0.3 ? "bg-amber-100 text-amber-700" :
                "bg-emerald-100 text-emerald-700"
              }`}>
                risk {((r.risk ?? 0)).toFixed(2)}
              </span>
              <span className={`text-sm font-medium uppercase px-2 py-1 rounded ${
                r.action === "bid" ? "bg-emerald-100 text-emerald-700" :
                r.action === "skip" ? "bg-slate-100 text-slate-500" :
                "bg-amber-100 text-amber-700"
              }`}>{r.action}</span>
              <Link className="text-sm underline text-blue-600 hover:text-blue-800" href={`/job/${r.job_id}`}>Open →</Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
