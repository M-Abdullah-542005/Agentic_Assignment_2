"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
export default function JobDetail({ params }: { params: { id: string } }) {
  const [d, setD] = useState<any>(null), [err, setErr] = useState("");
  useEffect(() => { api(`/jobs/${params.id}`).then(setD).catch(e => setErr(e.message)); }, [params.id]);
  if (err) return <p role="alert">{err}</p>; if (!d) return <p>Loading…</p>;
  const reqs: string[] = d.job_spec?.required_skills ?? [];
  return <div className="space-y-4"><h1 className="text-xl font-bold">{d.job_spec?.title}</h1>
    <p>{d.bid_decision?.action}: {d.bid_decision?.reason}</p>
    <p>Risk {d.risk_report?.score}: {d.risk_report?.explanation} {(d.risk_report?.flags ?? []).map((f: string) => <span key={f} className="ml-1 rounded bg-red-100 px-1">{f}</span>)}</p>
    <p>Price: {d.price_quote?.rationale}</p>
    <table className="w-full border bg-white text-sm"><thead><tr><th className="p-2 text-left">Requirement</th><th className="p-2 text-left">Evidence</th></tr></thead>
      <tbody>{reqs.map(q => {
        const qLower = q.toLowerCase();
        const matched = (d.evidence ?? []).filter((e: any) => {
          const skills: string[] = (e.skills ?? []).map((s: string) => s.toLowerCase());
          const text = (e.text ?? "").toLowerCase();
          const proj = (e.project ?? "").toLowerCase();
          return skills.some(s => s.includes(qLower) || qLower.includes(s)) || text.includes(qLower) || proj.includes(qLower);
        });
        const citedIds = matched.map((e: any) => e.evidence_id || e.chunk_id || e.id).filter(Boolean);
        return (
          <tr key={q} className="border-t">
            <td className="p-2 font-medium">{q}</td>
            <td className="p-2">
              {citedIds.length > 0 ? (
                <div className="flex flex-wrap gap-1">
                  {Array.from(new Set(citedIds)).map((id: any) => (
                    <span key={id} className="rounded bg-emerald-100 px-2 py-0.5 text-xs text-emerald-800 border border-emerald-300 font-mono">
                      {id}
                    </span>
                  ))}
                </div>
              ) : (
                <i className="text-amber-600">portfolio match pending</i>
              )}
            </td>
          </tr>
        );
      })}</tbody></table>
    {(d.status === "ready" || d.status === "approved")
      ? <Link className="underline" href={`/studio/${params.id}`}>Proposal studio</Link>
      : <Link className="rounded bg-blue-600 px-3 py-1.5 text-white text-sm" href={`/runs/${params.id}`}>Start Agent Run</Link>}
    {" · "}<Link className="underline" href={`/runs/${params.id}`}>Run viewer</Link></div>;
}
