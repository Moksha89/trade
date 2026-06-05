"use client";

import { useCallback, useEffect, useState } from "react";
import Shell from "@/components/Shell";
import {
  getDashboardStatus,
  scanNow,
  startBot,
  stopBot,
  emergency,
  type DashboardStatus,
} from "@/lib/api";

function fmt(v: number | null | undefined, prefix = ""): string {
  return v === null || v === undefined ? "—" : `${prefix}${(+v).toLocaleString()}`;
}

export default function DashboardPage() {
  const [status, setStatus] = useState<DashboardStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    getDashboardStatus()
      .then(setStatus)
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);

  async function action(fn: () => Promise<unknown>) {
    setBusy(true);
    try {
      await fn();
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell>
      <div className="row spread">
        <h2 className="page-title">Main Dashboard</h2>
        <div className="row">
          <span className="muted">Mode: {status?.execution_mode ?? "—"}</span>
          {status?.bot_running ? (
            <button className="secondary sm" disabled={busy} onClick={() => action(stopBot)}>
              Stop bot
            </button>
          ) : (
            <button className="sm" disabled={busy} onClick={() => action(startBot)}>
              Start bot
            </button>
          )}
          <button className="secondary sm" disabled={busy} onClick={() => action(scanNow)}>
            Scan now
          </button>
        </div>
      </div>

      {error && <div className="banner">{error}</div>}
      {status?.trading_locked && (
        <div className="banner">Trading locked: {status.lock_reason ?? "manual lock"}</div>
      )}

      {status && (
        <>
          <div className="grid">
            <Metric k="Bot" v={status.bot_running ? "Running" : "Stopped"} on={status.bot_running} />
            <Metric
              k="Broker"
              v={status.broker_connected ? "Connected" : "Disconnected"}
              on={status.broker_connected}
            />
            <Metric k="Auto mode" v={status.auto_mode_enabled ? "On" : "Off"} on={status.auto_mode_enabled} />
            <Metric k="Hedging" v={status.hedging_enabled ? "On" : "Off"} on={status.hedging_enabled} />
            <Metric k="Account balance" v={fmt(status.account_balance, "AED ")} />
            <Metric k="Available funds" v={fmt(status.available_funds, "AED ")} />
            <Metric k="Today P/L" v={fmt(status.today_pl, "AED ")} cls={plCls(status.today_pl)} />
            <Metric k="Weekly P/L" v={fmt(status.weekly_pl, "AED ")} cls={plCls(status.weekly_pl)} />
            <Metric k="Open trades" v={`${status.open_trades_count} / ${status.max_active_trades}`} />
            <Metric
              k="Open risk"
              v={`${fmt(status.current_open_risk)} / ${status.max_combined_open_risk}`}
            />
            <Metric
              k="Daily loss used"
              v={`${fmt(status.daily_loss_limit_used)} / ${status.daily_loss_limit}`}
            />
            <Metric k="Last AI decision" v={status.last_ai_decision ?? "—"} />
          </div>

          <div className="card" style={{ marginTop: 24 }}>
            <div className="row spread">
              <div>
                <strong>Emergency Stop</strong>
                <p className="muted" style={{ margin: "6px 0 0" }}>
                  Halts the bot and disables auto-trading immediately. Open positions stay managed.
                </p>
              </div>
              <button className="danger" disabled={busy} onClick={() => action(() => emergency("stop-bot"))}>
                Emergency Stop
              </button>
            </div>
            {status.last_risk_rejection_reason && (
              <p className="muted" style={{ marginTop: 12 }}>
                Last risk rejection: {status.last_risk_rejection_reason}
              </p>
            )}
          </div>
        </>
      )}
    </Shell>
  );
}

function plCls(v: number | null): string | undefined {
  if (v === null || v === undefined || v === 0) return undefined;
  return v > 0 ? "pos" : "neg";
}

function Metric({ k, v, on, cls }: { k: string; v: string; on?: boolean; cls?: string }) {
  return (
    <div className="metric">
      <div className="k">{k}</div>
      <div className="v">
        {on === undefined ? <span className={cls}>{v}</span> : <span className={`pill ${on ? "on" : "off"}`}>{v}</span>}
      </div>
    </div>
  );
}
