"use client";

import { useCallback, useEffect, useState } from "react";
import Shell from "@/components/Shell";
import { closeTrade, listTrades, moveSL, moveTP, type Trade } from "@/lib/api";

export default function TradesPage() {
  const [open, setOpen] = useState<Trade[]>([]);
  const [closed, setClosed] = useState<Trade[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    listTrades("open").then(setOpen).catch((e) => setError(e.message));
    listTrades("closed").then(setClosed).catch(() => undefined);
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function promptMove(kind: "sl" | "tp", t: Trade) {
    const cur = kind === "sl" ? t.stop_loss : t.take_profit_1;
    const val = window.prompt(`New ${kind.toUpperCase()} for ${t.instrument}`, String(round(cur)));
    if (!val) return;
    const num = parseFloat(val);
    if (Number.isNaN(num)) return;
    await act(() => (kind === "sl" ? moveSL(t.id, num) : moveTP(t.id, num)));
  }

  return (
    <Shell>
      <h2 className="page-title">Open Trades</h2>
      {error && <div className="banner">{error}</div>}
      <div className="card">
        <table>
          <thead>
            <tr>
              <th>Instr</th><th>Dir</th><th>Entry</th><th>Current</th><th>Size</th>
              <th>SL</th><th>TP1</th><th>R</th><th>uP/L</th><th>Mgmt</th><th></th>
            </tr>
          </thead>
          <tbody>
            {open.length === 0 && (
              <tr><td colSpan={11} className="muted">No open trades.</td></tr>
            )}
            {open.map((t) => (
              <tr key={t.id}>
                <td>{t.instrument}</td>
                <td className={t.direction === "long" ? "long" : "short"}>{t.direction}</td>
                <td>{round(t.entry_price)}</td>
                <td>{round(t.current_price)}</td>
                <td>{round(t.size, 4)}</td>
                <td>{round(t.stop_loss)}</td>
                <td>{round(t.take_profit_1)}</td>
                <td className={t.r_multiple >= 0 ? "pos" : "neg"}>{t.r_multiple}</td>
                <td className={t.unrealized_pl >= 0 ? "pos" : "neg"}>{round(t.unrealized_pl)}</td>
                <td className="muted">{mgmt(t)}</td>
                <td className="row">
                  <button className="sm secondary" disabled={busy} onClick={() => promptMove("sl", t)}>SL</button>
                  <button className="sm secondary" disabled={busy} onClick={() => promptMove("tp", t)}>TP</button>
                  <button className="sm danger" disabled={busy} onClick={() => act(() => closeTrade(t.id))}>Close</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3 style={{ marginTop: 28 }}>Recently Closed</h3>
      <div className="card">
        <table>
          <thead>
            <tr><th>Instr</th><th>Dir</th><th>Entry</th><th>Size</th><th>Realized P/L</th><th>Reason</th><th>Closed</th></tr>
          </thead>
          <tbody>
            {closed.length === 0 && <tr><td colSpan={7} className="muted">None.</td></tr>}
            {closed.slice(0, 20).map((t) => (
              <tr key={t.id}>
                <td>{t.instrument}</td>
                <td className={t.direction === "long" ? "long" : "short"}>{t.direction}</td>
                <td>{round(t.entry_price)}</td>
                <td>{round(t.size, 4)}</td>
                <td className={t.realized_pl >= 0 ? "pos" : "neg"}>{round(t.realized_pl)}</td>
                <td className="muted">{t.close_reason}</td>
                <td className="muted">{t.closed_at?.replace("T", " ").slice(0, 16)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Shell>
  );
}

function mgmt(t: Trade): string {
  const parts: string[] = [];
  if (t.breakeven_moved) parts.push("BE");
  if (t.profit_locked) parts.push("+0.5R");
  if (t.partial_closed) parts.push("partial");
  return parts.length ? parts.join(" · ") : "—";
}

function round(v: number, dp = 2): string {
  if (v === null || v === undefined) return "—";
  return (+v).toLocaleString(undefined, { maximumFractionDigits: dp });
}
