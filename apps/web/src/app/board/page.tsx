"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

const COLS = ["drafted", "submitted", "viewed", "replied", "interview", "hired", "lost"];

export default function Board() {
  const [rows, setRows] = useState<any[]>([]);
  const [hasToken, setHasToken] = useState(true);

  useEffect(() => {
    const token = typeof window !== "undefined" ? localStorage.getItem("token") : "";
    if (!token) {
      setHasToken(false);
      return;
    }
    const t = () => {
      api("/jobs")
        .then(setRows)
        .catch(e => {
          if (e.message && e.message.includes("401")) setHasToken(false);
        });
    };
    t();
    const i = setInterval(t, 5000);
    return () => clearInterval(i);
  }, []);

  if (!hasToken) {
    return (
      <div className="rounded border bg-white p-6 text-center space-y-3">
        <h2 className="text-lg font-bold text-slate-800">Not logged in yet</h2>
        <p className="text-sm text-slate-600">Please sign in or create an account to view your pipeline board.</p>
        <Link href="/" className="inline-block rounded bg-emerald-600 px-4 py-2 text-white text-sm font-medium">Go to Login</Link>
      </div>
    );
  }

  const col = (r: any) => {
    if (r.outcome_stage && COLS.includes(r.outcome_stage)) return r.outcome_stage;
    if (r.status === "submitted") return "submitted";
    if (r.status === "rejected" || r.status === "skipped") return "lost";
    return "drafted";
  };

  return <div className="grid grid-cols-2 gap-2 md:grid-cols-7">{COLS.map(c => <section key={c} className="rounded bg-slate-100 p-2">
    <h2 className="text-xs font-bold uppercase">{c}</h2>{rows.filter(r => col(r) === c).map(r => <div key={r.job_id} className="mt-1 rounded bg-white p-2 text-xs">{r.title}</div>)}</section>)}</div>;
}
