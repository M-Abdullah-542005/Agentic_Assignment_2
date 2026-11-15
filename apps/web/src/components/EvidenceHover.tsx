"use client";
import { useState } from "react";
export type Evidence = { id: string; source?: string; type?: string; date?: string; outcome?: string; text?: string };
export default function EvidenceHover({ text, ids, evidence }: { text: string; ids: string[]; evidence: Evidence[] }) {
  const [on, setOn] = useState(false);
  const found = evidence.filter(e => ids.includes(e.id));
  return (
    <span tabIndex={0} onMouseEnter={() => setOn(true)} onMouseLeave={() => setOn(false)} onFocus={() => setOn(true)} onBlur={() => setOn(false)}
      className={`relative cursor-help rounded px-0.5 ${found.length ? "bg-emerald-100" : "bg-red-100"}`} data-testid="claim">
      {text}
      {on && <span role="tooltip" className="absolute left-0 top-full z-10 w-72 rounded border bg-white p-2 text-xs shadow">
        {found.length ? found.map(e => <span key={e.id} className="block"><b>{e.id}</b> · {e.type} · {e.source} {e.outcome && `· ${e.outcome}`}<br />{e.text?.slice(0, 120)}</span>) : "No evidence: unsupported"}
      </span>}
    </span>);
}
