"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type AnalyticsData = {
  total_jobs: number;
  bids: number;
  skipped: number;
  submitted: number;
  win_rate: number;
  avg_fit: number;
  avg_risk: number;
  total_connects_used: number;
};

export default function Analytics() {
  const [data, setData] = useState<AnalyticsData | null>(null);
  const [jobs, setJobs] = useState<any[]>([]);
  const [err, setErr] = useState("");

  useEffect(() => {
    const token = typeof window !== "undefined" ? localStorage.getItem("token") : "";
    if (!token) { setErr("Not logged in"); return; }
    api<any[]>("/jobs")
      .then((rows) => {
        setJobs(rows);
        const bids     = rows.filter(r => r.action === "bid").length;
        const skipped  = rows.filter(r => r.action === "skip").length;
        const submitted= rows.filter(r => r.status === "submitted").length;
        const hired    = rows.filter(r => r.outcome_stage === "hired").length;
        const avgFit   = rows.length ? rows.reduce((s, r) => s + (r.fit ?? 0), 0) / rows.length : 0;
        const avgRisk  = rows.length ? rows.reduce((s, r) => s + (r.risk ?? 0), 0) / rows.length : 0;
        const connects = rows.reduce((s, r) => s + (r.connects ?? 0), 0);
        setData({
          total_jobs: rows.length, bids, skipped, submitted,
          win_rate: submitted > 0 ? hired / submitted : 0,
          avg_fit: avgFit, avg_risk: avgRisk,
          total_connects_used: connects,
        });
      })
      .catch(e => setErr(e.message));
  }, []);

  if (err) return <p role="alert" className="text-red-600 p-4">{err}</p>;
  if (!data) return <p className="p-4 text-slate-500">Loading analytics…</p>;

  const stats = [
    { label: "Total Jobs Scouted", value: data.total_jobs },
    { label: "Bids",               value: data.bids, color: "text-emerald-600" },
    { label: "Skipped",            value: data.skipped, color: "text-slate-500" },
    { label: "Submitted",          value: data.submitted, color: "text-blue-600" },
    { label: "Win Rate",           value: `${(data.win_rate * 100).toFixed(1)}%` },
    { label: "Avg Fit Score",      value: `${(data.avg_fit * 100).toFixed(1)}%` },
    { label: "Avg Risk Score",     value: `${(data.avg_risk * 100).toFixed(1)}%` },
    { label: "Connects Used",      value: data.total_connects_used },
  ];

  return (
    <div className="space-y-6 p-4">
      <h1 className="text-xl font-bold text-slate-800">Analytics Dashboard</h1>

      {/* KPI cards */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {stats.map(s => (
          <div key={s.label} className="rounded border bg-white p-4 shadow-sm text-center">
            <p className="text-xs text-slate-500 uppercase tracking-wide">{s.label}</p>
            <p className={`text-2xl font-bold mt-1 ${(s as any).color ?? "text-slate-800"}`}>{s.value}</p>
          </div>
        ))}
      </div>

      {/* Action breakdown */}
      <div>
        <h2 className="text-sm font-semibold text-slate-700 mb-2">Action Breakdown</h2>
        <div className="flex gap-2 items-center">
          {["bid", "skip", "watch"].map(a => {
            const count = jobs.filter(r => r.action === a).length;
            const pct = jobs.length ? Math.round(count / jobs.length * 100) : 0;
            return (
              <div key={a} className="flex-1 rounded bg-slate-100 p-2 text-center text-sm">
                <span className="font-medium capitalize">{a}</span>
                <div className="mt-1 text-slate-600">{count} ({pct}%)</div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Top bid jobs */}
      {jobs.filter(r => r.action === "bid").length > 0 && (
        <div>
          <h2 className="text-sm font-semibold text-slate-700 mb-2">Top Bid Jobs</h2>
          <ul className="space-y-1">
            {jobs.filter(r => r.action === "bid").slice(0, 5).map(r => (
              <li key={r.job_id} className="flex justify-between rounded border bg-white p-2 text-sm">
                <span className="truncate">{r.title}</span>
                <span className="text-emerald-600 font-medium ml-2">fit {((r.fit ?? 0) * 100).toFixed(0)}%</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
