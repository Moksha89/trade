"use client";

import { useState } from "react";
import Shell from "@/components/Shell";
import { emergency } from "@/lib/api";

const ACTIONS: { action: string; label: string; desc: string; danger?: boolean }[] = [
  { action: "stop-bot", label: "Stop bot", desc: "Halt the loop and disable auto-trading.", danger: true },
  { action: "disable-auto", label: "Disable auto trading", desc: "Keep bot running but stop auto-execution." },
  { action: "close-all", label: "Close all positions", desc: "Market-close every open trade now.", danger: true },
  { action: "lock-today", label: "Lock trading for today", desc: "Reject all new trades until unlocked." },
  { action: "unlock", label: "Unlock trading", desc: "Clear today's trading lock." },
  { action: "disable-hedging", label: "Disable hedging", desc: "Force hedging mode off." },
  { action: "disconnect-broker", label: "Disconnect broker", desc: "Drop the Capital.com session." },
];

export default function EmergencyPage() {
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function run(action: string, label: string) {
    if (!window.confirm(`${label}?`)) return;
    setBusy(action);
    setError(null);
    setMsg(null);
    try {
      await emergency(action);
      setMsg(`${label} — done.`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Shell>
      <h2 className="page-title">Emergency Panel</h2>
      {error && <div className="banner">{error}</div>}
      {msg && (
        <div className="banner" style={{ background: "rgba(46,204,113,0.12)", borderColor: "var(--green)", color: "var(--green)" }}>
          {msg}
        </div>
      )}
      <div className="grid">
        {ACTIONS.map((a) => (
          <div className="card" key={a.action}>
            <strong>{a.label}</strong>
            <p className="muted" style={{ margin: "8px 0 14px", fontSize: 13 }}>{a.desc}</p>
            <button
              className={a.danger ? "danger" : "secondary"}
              disabled={busy !== null}
              onClick={() => run(a.action, a.label)}
            >
              {busy === a.action ? "Working…" : a.label}
            </button>
          </div>
        ))}
      </div>
    </Shell>
  );
}
