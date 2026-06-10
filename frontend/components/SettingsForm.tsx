"use client";

import { useEffect, useState } from "react";
import { getSettings, putSettings } from "@/lib/api";

type Val = boolean | number | string | string[];

export default function SettingsForm({ group, title }: { group: string; title: string }) {
  const [data, setData] = useState<Record<string, Val> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getSettings(group)
      .then((d) => setData(d as Record<string, Val>))
      .catch((e) => setError(e.message));
  }, [group]);

  function set(k: string, v: Val) {
    setData((d) => (d ? { ...d, [k]: v } : d));
    setSaved(false);
  }

  async function save() {
    if (!data) return;
    setBusy(true);
    setError(null);
    try {
      const merged = await putSettings(group, data);
      setData(merged as Record<string, Val>);
      setSaved(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="row spread">
        <h2 className="page-title">{title}</h2>
        <button disabled={busy || !data} onClick={save}>
          {busy ? "Saving…" : "Save changes"}
        </button>
      </div>
      {error && <div className="banner">{error}</div>}
      {saved && <div className="banner" style={{ background: "rgba(46,204,113,0.12)", borderColor: "var(--green)", color: "var(--green)" }}>Saved.</div>}
      {!data && <p className="muted">Loading…</p>}
      {data && (
        <div className="card">
          {Object.entries(data).map(([k, v]) => (
            <div className="toggle-row" key={k}>
              <label htmlFor={k}>{label(k)}</label>
              {renderInput(k, v, set)}
            </div>
          ))}
        </div>
      )}
    </>
  );
}

function renderInput(k: string, v: Val, set: (k: string, v: Val) => void) {
  if (typeof v === "boolean") {
    return <input id={k} type="checkbox" checked={v} onChange={(e) => set(k, e.target.checked)} />;
  }
  if (typeof v === "number") {
    return (
      <input
        id={k}
        className="num-input"
        type="number"
        step="any"
        value={v}
        onChange={(e) => set(k, e.target.value === "" ? 0 : parseFloat(e.target.value))}
      />
    );
  }
  if (Array.isArray(v)) {
    return (
      <input
        id={k}
        style={{ width: 280 }}
        value={v.join(", ")}
        onChange={(e) => set(k, e.target.value.split(",").map((s) => s.trim()).filter(Boolean))}
      />
    );
  }
  return <input id={k} style={{ width: 220 }} value={v} onChange={(e) => set(k, e.target.value)} />;
}

function label(k: string): string {
  return k.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
