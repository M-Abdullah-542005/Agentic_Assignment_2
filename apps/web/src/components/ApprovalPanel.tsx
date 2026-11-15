"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type Approval = {
  job_id: string;
  draft: { cover_letter: string };
  status: string;
};

type Props = { jobId: string; onApproved?: () => void; onRejected?: () => void };

export default function ApprovalPanel({ jobId, onApproved, onRejected }: Props) {
  const [data, setData]       = useState<Approval | null>(null);
  const [text, setText]       = useState("");
  const [reason, setReason]   = useState("");
  const [editing, setEditing] = useState(false);
  const [msg, setMsg]         = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!jobId) return;
    api<Approval>(`/proposals/${jobId}`)
      .then(d => { setData(d); setText(d.draft.cover_letter); })
      .catch(() => {});
  }, [jobId]);

  const act = async (body: object) => {
    setLoading(true); setMsg("");
    try {
      const r = await api(`/approvals/${jobId}`, { method: "POST", body: JSON.stringify(body) });
      setMsg(r.pending_again ? "Edit saved — compliance re-checked." : `Done: ${r.status}`);
      if (r.status === "approved" && onApproved) onApproved();
      if (r.status === "rejected" && onRejected) onRejected();
    } catch (e: any) { setMsg(e.message); }
    finally { setLoading(false); }
  };

  if (!data) return (
    <div className="rounded border bg-slate-50 p-4 text-sm text-slate-500">
      No proposal draft available yet.
    </div>
  );

  return (
    <div className="space-y-4 rounded border bg-white p-4 shadow-sm">
      <h2 className="font-semibold text-slate-800">Proposal Draft</h2>

      {/* Draft text */}
      {editing ? (
        <textarea
          className="h-48 w-full rounded border p-2 text-sm font-mono focus:outline-blue-400"
          value={text}
          onChange={e => setText(e.target.value)}
        />
      ) : (
        <div className="rounded bg-slate-50 p-3 text-sm whitespace-pre-wrap leading-relaxed">
          {data.draft.cover_letter}
        </div>
      )}

      {/* Status */}
      {msg && (
        <p role="status" className="rounded bg-blue-50 border border-blue-200 p-2 text-sm text-blue-700">
          {msg}
        </p>
      )}

      {/* Actions */}
      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => act({ action: "approve" })}
          disabled={loading}
          className="rounded bg-emerald-600 px-4 py-2 text-sm text-white hover:bg-emerald-700 disabled:opacity-50"
        >
          ✓ Approve & Send
        </button>

        {editing ? (
          <button
            onClick={() => { act({ action: "edit", text }); setEditing(false); }}
            disabled={loading}
            className="rounded border px-4 py-2 text-sm hover:bg-slate-50 disabled:opacity-50"
          >
            Save Edit
          </button>
        ) : (
          <button
            onClick={() => setEditing(true)}
            className="rounded border px-4 py-2 text-sm hover:bg-slate-50"
          >
            ✎ Edit
          </button>
        )}

        <div className="flex items-center gap-2 ml-auto">
          <input
            placeholder="Rejection reason…"
            className="rounded border px-3 py-2 text-sm w-48"
            value={reason}
            onChange={e => setReason(e.target.value)}
          />
          <button
            onClick={() => act({ action: "reject", reason })}
            disabled={loading || !reason.trim()}
            className="rounded bg-red-600 px-4 py-2 text-sm text-white hover:bg-red-700 disabled:opacity-50"
          >
            ✗ Reject
          </button>
        </div>
      </div>
    </div>
  );
}
