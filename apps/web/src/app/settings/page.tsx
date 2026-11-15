"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

const PERSONAS = ["ai_ml_engineer", "fullstack_dev", "data_analyst"];

export default function Settings() {
  const [me, setMe]         = useState<any>(null);
  const [persona, setPersona] = useState("");
  const [rate, setRate]     = useState(50);
  const [budget, setBudget] = useState(60);
  const [token, setToken]   = useState("");
  const [msg, setMsg]       = useState("");
  const [err, setErr]       = useState("");

  useEffect(() => {
    api("/auth/me")
      .then(u => { setMe(u); setPersona(u.persona ?? ""); setRate(u.hourly_rate ?? 50); setBudget(u.weekly_connects_budget ?? 60); })
      .catch(e => setErr(e.message));
  }, []);

  const save = async () => {
    setMsg(""); setErr("");
    try {
      await api("/auth/session", {
        method: "POST",
        body: JSON.stringify({
          email: me.email, password: "unchanged_keep_session",
          persona, hourly_rate: rate, weekly_connects_budget: budget,
        }),
      });
      setMsg("Profile updated ✓");
    } catch (e: any) { setErr(e.message); }
  };

  const connectMarketplace = async () => {
    setMsg(""); setErr("");
    try {
      await api("/auth/marketplace-token", { method: "POST", body: JSON.stringify({ token }) });
      setMsg("Marketplace token saved ✓");
      setToken("");
    } catch (e: any) { setErr(e.message); }
  };

  const logout = async () => {
    try { await api("/auth/logout", { method: "POST" }); } catch {}
    localStorage.removeItem("token");
    window.location.href = "/";
  };

  if (err && !me) return <p role="alert" className="p-4 text-red-600">{err}</p>;
  if (!me) return <p className="p-4 text-slate-500">Loading…</p>;

  return (
    <div className="max-w-lg space-y-6 p-4">
      <h1 className="text-xl font-bold text-slate-800">Settings</h1>

      {/* Profile */}
      <section className="rounded border bg-white p-4 space-y-3">
        <h2 className="font-semibold text-slate-700">Profile</h2>
        <p className="text-sm text-slate-500">{me.email}</p>

        <div>
          <label className="block text-sm font-medium mb-1">Persona</label>
          <div className="flex flex-wrap gap-2">
            {PERSONAS.map(p => (
              <button
                key={p} onClick={() => setPersona(p)}
                className={`rounded border px-3 py-1.5 text-sm transition ${persona === p ? "bg-slate-900 text-white" : "bg-slate-50 hover:bg-slate-100"}`}
              >{p}</button>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <label className="block text-sm">
            <span className="font-medium">Hourly Rate ($)</span>
            <input type="number" className="mt-1 w-full rounded border p-2"
              value={rate} onChange={e => setRate(+e.target.value)} />
          </label>
          <label className="block text-sm">
            <span className="font-medium">Weekly Connects</span>
            <input type="number" className="mt-1 w-full rounded border p-2"
              value={budget} onChange={e => setBudget(+e.target.value)} />
          </label>
        </div>
        <button onClick={save} className="rounded bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700">
          Save profile
        </button>
      </section>

      {/* Marketplace token */}
      <section className="rounded border bg-white p-4 space-y-3">
        <h2 className="font-semibold text-slate-700">Marketplace Connection</h2>
        <p className="text-sm text-slate-500">
          Status: <span className={me.marketplace_connected ? "text-emerald-600 font-medium" : "text-red-500"}>
            {me.marketplace_connected ? "Connected ✓" : "Not connected"}
          </span>
        </p>
        <input
          type="password" placeholder="Paste marketplace API token"
          className="w-full rounded border p-2 text-sm"
          value={token} onChange={e => setToken(e.target.value)}
        />
        <button onClick={connectMarketplace} disabled={!token}
          className="rounded bg-emerald-600 px-4 py-2 text-sm text-white disabled:opacity-50 hover:bg-emerald-700">
          Save token
        </button>
      </section>

      {/* Feedback */}
      {msg && <p className="text-emerald-600 text-sm">{msg}</p>}
      {err && <p role="alert" className="text-red-600 text-sm">{err}</p>}

      {/* Logout */}
      <button onClick={logout} className="text-sm text-slate-500 underline hover:text-red-600">
        Sign out
      </button>
    </div>
  );
}
