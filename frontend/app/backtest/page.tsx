"use client";

import { useState } from "react";
import Shell from "@/components/Shell";
import { runBacktest, type BacktestReport } from "@/lib/api";

const INSTRUMENTS = ["US100", "US500", "Gold"];

export default function BacktestPage() {
  const [instrument, setInstrument] = useState("US100");
  const [bars, setBars] = useState(600);
  const [report, setReport] = useState<BacktestReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      setReport(await runBacktest(instrument, bars));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell>
      <h2 className="page-title">Backtest / Forward Test</h2>
      {error && <div className="banner">{error}</div>}
      <div className="card">
        <div className="row">
          <label>Instrument</label>
          <select
            value={instrument}
            onChange={(e) => setInstrument(e.target.value)}
            style={{ background: "var(--panel-2)", color: "var(--text)", border: "1px solid var(--border)", borderRadius: 8, padding: "9px 12px" }}
          >
            {INSTRUMENTS.map((i) => (
              <option key={i} value={i}>{i}</option>
            ))}
          </select>
          <label>Bars</label>
          <input className="num-input" type="number" value={bars} onChange={(e) => setBars(parseInt(e.target.value || "0", 10))} />
          <button disabled={busy} onClick={run}>{busy ? "Running…" : "Run backtest"}</button>
        </div>
      </div>

      {report && (
        <>
          <div className="grid" style={{ marginTop: 20 }}>
            <M k="Trades" v={report.trades} />
            <M k="Win rate" v={`${report.win_rate}%`} />
            <M k="Profit factor" v={report.profit_factor} />
            <M k="Avg R" v={report.avg_r} />
            <M k="Total R" v={report.total_r} />
            <M k="Max drawdown (R)" v={report.max_drawdown_r} />
            <M k="Best trade (R)" v={report.best_trade_r} />
            <M k="Worst trade (R)" v={report.worst_trade_r} />
          </div>
          <h3 style={{ marginTop: 24 }}>By strategy</h3>
          <div className="card">
            <table>
              <thead><tr><th>Strategy</th><th>Trades</th><th>Win rate</th><th>Total R</th></tr></thead>
              <tbody>
                {Object.entries(report.by_strategy).map(([name, s]) => (
                  <tr key={name}>
                    <td>{name}</td>
                    <td>{s.trades}</td>
                    <td>{s.win_rate}%</td>
                    <td className={s.total_r >= 0 ? "pos" : "neg"}>{s.total_r}</td>
                  </tr>
                ))}
                {Object.keys(report.by_strategy).length === 0 && (
                  <tr><td colSpan={4} className="muted">No trades generated.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Shell>
  );
}

function M({ k, v }: { k: string; v: string | number }) {
  return (
    <div className="metric">
      <div className="k">{k}</div>
      <div className="v">{v}</div>
    </div>
  );
}
