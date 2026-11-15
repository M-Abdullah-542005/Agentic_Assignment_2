"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import EvidenceHover from "@/components/EvidenceHover";
export default function Studio({ params }: { params: { id: string } }) {
  const id = params.id; const [p, setP] = useState<any>(null), [editing, setEditing] = useState(false);
  const [text, setText] = useState(""), [reason, setReason] = useState(""), [msg, setMsg] = useState(""), [diff, setDiff] = useState("");
  const [noDraft, setNoDraft] = useState(false), [starting, setStarting] = useState(false);
  const router = useRouter();
  const load = () => api(`/proposals/${id}`).then(x => { setP(x); setText(x.draft.cover_letter); }).catch(e => {
    if (e.message?.includes("404") || e.status === 404) { setNoDraft(true); } else { setMsg(e.message); }
  });
  useEffect(() => { load(); }, [id]);
  const startRun = async () => {
    setStarting(true);
    try {
      // /runs/{id} returns SSE not JSON — use raw fetch to just kick it off
      const token = typeof window !== "undefined" ? localStorage.getItem("token") ?? "" : "";
      await fetch(`${process.env.NEXT_PUBLIC_API ?? "http://localhost:8000"}/runs/${id}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      router.push(`/runs/${id}`);
    } catch (e: any) { setMsg(e.message); setStarting(false); }
  };
  const act = async (body: any) => { try { const r = await api(`/approvals/${id}`, { method: "POST", body: JSON.stringify(body) });
    setMsg(r.pending_again ? "Edit saved; Compliance re-checked. Approve again to send." : `Done: ${r.status}`); setDiff(r.diff ?? ""); load(); } catch (e: any) { setMsg(e.message); } };
  if (noDraft) return (
    <div className="space-y-4 p-6 text-center">
      <p className="text-lg text-gray-700">No proposal draft yet.</p>
      <p className="text-sm text-gray-500">The full agent pipeline (writer → verifier → critic → compliance) hasn't run for this job yet.</p>
      <button
        data-testid="start-run"
        className="rounded bg-blue-600 px-4 py-2 text-white disabled:opacity-50"
        onClick={startRun}
        disabled={starting}
      >
        {starting ? "Starting…" : "Start Analysis"}
      </button>
      {msg && <p role="alert" className="text-red-600 text-sm">{msg}</p>}
    </div>
  );
  if (!p) return <p>{msg || "Loading…"}</p>;
  const claims: any[] = p.claims ?? [];
  const paras = p.draft.cover_letter.split("\n\n");
  return <div className="space-y-3">
    {p.dry_run && <pre data-testid="dry-run" className="rounded bg-slate-100 p-2 text-xs">{JSON.stringify(p.dry_run, null, 2)}</pre>}
    {editing ? <textarea data-testid="edit-text" className="h-64 w-full rounded border p-2" value={text} onChange={e => setText(e.target.value)} />
      : paras.map((para: string, i: number) => <p key={i} className="rounded bg-white p-2">
          {claims.filter(c => para.includes(c.claim)).length
            ? claims.filter(c => para.includes(c.claim)).map((c, j) => <EvidenceHover key={j} text={c.claim + " "} ids={c.evidence_ids ?? []} evidence={p.evidence} />)
            : para}
          <button className="ml-2 text-xs underline" onClick={async () => { await api(`/proposals/${id}/regenerate`, { method: "POST", body: JSON.stringify({ paragraph: i }) }); load(); }}>regenerate</button></p>)}
    <ul>{p.comments.map((c: any, i: number) => <li key={i} className={c.by === "critic" ? "text-amber-700" : "text-red-700"}>[{c.by}] {c.text}</li>)}</ul>
    {diff && <pre data-testid="diff" className="rounded bg-slate-900 p-2 text-xs text-green-300">{diff}</pre>}
    {msg && <p role="status" data-testid="status">{msg}</p>}
    <div className="flex flex-wrap gap-2">
      <button data-testid="approve" className="rounded bg-emerald-600 px-3 py-2 text-white" onClick={() => act({ action: "approve" })}>Approve &amp; send</button>
      {editing ? <button data-testid="save-edit" className="rounded border px-3 py-2" onClick={() => { act({ action: "edit", text }); setEditing(false); }}>Save edit</button>
        : <button data-testid="edit" className="rounded border px-3 py-2" onClick={() => setEditing(true)}>Edit</button>}
      <input data-testid="reason" placeholder="Rejection reason" className="rounded border p-2" value={reason} onChange={e => setReason(e.target.value)} />
      <button data-testid="reject" className="rounded bg-red-600 px-3 py-2 text-white" onClick={() => act({ action: "reject", reason })}>Reject</button></div></div>;
}
