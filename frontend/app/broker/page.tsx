"use client";

import { useCallback, useEffect, useState } from "react";
import Shell from "@/components/Shell";
import { brokerReconnect, brokerStatus } from "@/lib/api";

export default function BrokerPage() {
  const [info, setInfo] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    brokerStatus().then(setInfo).catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function reconnect() {
    setBusy(true);
    setError(null);
    setMsg(null);
    try {
      const r = (await brokerReconnect()) as Record<string, unknown>;
      setMsg(`Connected. Balance: ${JSON.stringify(r.balance)}`);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell>
      <h2 className="page-title">Capital.com Connection</h2>
      {error && <div className="banner">{error}</div>}
      {msg && (
        <div className="banner" style={{ background: "rgba(46,204,113,0.12)", borderColor: "var(--green)", color: "var(--green)" }}>
          {msg}
        </div>
      )}
      <div className="card">
        {info ? (
          <table>
            <tbody>
              {Object.entries(info).map(([k, v]) => (
                <tr key={k}>
                  <td className="muted">{k.replace(/_/g, " ")}</td>
                  <td>{typeof v === "boolean" ? (v ? "yes" : "no") : String(v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">Loading…</p>
        )}
        <div className="row" style={{ marginTop: 16 }}>
          <button disabled={busy} onClick={reconnect}>Reconnect / refresh session</button>
        </div>
        <p className="muted" style={{ marginTop: 12 }}>
          Demo/live mode is set via <code>CAPITAL_ENVIRONMENT</code>. Connecting requires API key,
          identifier and password in the server environment.
        </p>
      </div>
    </Shell>
  );
}
