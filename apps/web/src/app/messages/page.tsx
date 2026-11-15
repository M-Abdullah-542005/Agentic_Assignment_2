"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function Messages() {
  const [cs, setCs] = useState<any[]>([]), [err, setErr] = useState("");
  const [hasToken, setHasToken] = useState(true);

  const load = () => {
    const token = typeof window !== "undefined" ? localStorage.getItem("token") : "";
    if (!token) {
      setHasToken(false);
      return;
    }
    api("/messages")
      .then(setCs)
      .catch(e => {
        if (e.message && e.message.includes("401")) {
          setHasToken(false);
        } else {
          setErr(e.message);
        }
      });
  };

  useEffect(() => { load(); }, []);

  if (!hasToken) {
    return (
      <div className="rounded border bg-white p-6 text-center space-y-3">
        <h2 className="text-lg font-bold text-slate-800">Not logged in yet</h2>
        <p className="text-sm text-slate-600">Please sign in or create an account to view client messages.</p>
        <Link href="/" className="inline-block rounded bg-emerald-600 px-4 py-2 text-white text-sm font-medium">Go to Login</Link>
      </div>
    );
  }

  const act = (id: string, body: any) => api(`/messages/${id}/approval`, { method: "POST", body: JSON.stringify(body) }).then(load).catch(e => setErr(e.message));
  if (!cs.length) return <p>No conversations.</p>;
  return <div className="space-y-3">{err && <p role="alert">{err}</p>}{cs.map(c => <article key={c.id} className="rounded border bg-white p-3">
    <p className="text-xs uppercase">{c.intent}</p>{c.messages.map((m: any, i: number) => <p key={i}>{m.text}</p>)}
    {c.pending && <><blockquote className="my-2 border-l-4 p-2">{c.draft}{c.slots.length > 0 && <ul>{c.slots.map((s: string) => <li key={s}>{s}</li>)}</ul>}</blockquote>
      <button className="mr-2 rounded bg-emerald-600 px-3 py-1 text-white" onClick={() => act(c.id, { action: "approve" })}>Approve &amp; send</button>
      <button className="rounded bg-red-600 px-3 py-1 text-white" onClick={() => act(c.id, { action: "reject", reason: prompt("Reason?") || "n/a" })}>Reject</button></>}</article>)}</div>;
}
