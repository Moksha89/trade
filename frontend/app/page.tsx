"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  clearToken,
  getDashboardStatus,
  getToken,
  type DashboardStatus,
} from "@/lib/api";

function fmt(v: number | null, prefix = ""): string {
  return v === null || v === undefined ? "—" : `${prefix}${v}`;
}

export default function DashboardPage() {
  const router = useRouter();
  const [status, setStatus] = useState<DashboardStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    getDashboardStatus()
      .then(setStatus)
      .catch((err) => {
        if (err.message === "unauthorized") {
          router.replace("/login");
        } else {
          setError("Failed to load dashboard status");
        }
      });
  }, [router]);

  function logout() {
    clearToken();
    router.replace("/login");
  }

  if (error) return <div className="shell">{error}</div>;
  if (!status) return <div className="shell">Loading…</div>;

  return (
    <div className="shell">
      <div className="topbar">
        <div>
          <h2 style={{ margin: 0 }}>Main Dashboard</h2>
          <span style={{ color: "var(--muted)", fontSize: 13 }}>
            Mode: {status.execution_mode} · Phase 0 scaffold
          </span>
        </div>
        <button className="secondary" onClick={logout}>
          Log out
        </button>
      </div>

      <div className="grid">
        <Metric k="Bot" v={status.bot_running ? "Running" : "Stopped"} on={status.bot_running} />
        <Metric
          k="Broker connection"
          v={status.broker_connected ? "Connected" : "Disconnected"}
          on={status.broker_connected}
        />
        <Metric k="Auto mode" v={status.auto_mode_enabled ? "On" : "Off"} on={status.auto_mode_enabled} />
        <Metric k="Hedging" v={status.hedging_enabled ? "On" : "Off"} on={status.hedging_enabled} />
        <Metric k="Account balance" v={fmt(status.account_balance, "AED ")} />
        <Metric k="Available funds" v={fmt(status.available_funds, "AED ")} />
        <Metric k="Today P/L" v={fmt(status.today_pl, "AED ")} />
        <Metric k="Weekly P/L" v={fmt(status.weekly_pl, "AED ")} />
        <Metric k="Open trades" v={`${status.open_trades_count} / ${status.max_active_trades}`} />
        <Metric k="Current open risk" v={fmt(status.current_open_risk, "AED ")} />
        <Metric k="Daily loss used" v={fmt(status.daily_loss_limit_used, "AED ")} />
        <Metric k="Last AI decision" v={status.last_ai_decision ?? "—"} />
      </div>

      <div className="card" style={{ marginTop: 24 }}>
        <strong>Emergency</strong>
        <p style={{ color: "var(--muted)", marginBottom: 12 }}>
          Kill switch and trade controls will be wired up in Phase 4.
        </p>
        <button className="danger" disabled>
          Emergency Stop
        </button>
      </div>
    </div>
  );
}

function Metric({ k, v, on }: { k: string; v: string; on?: boolean }) {
  return (
    <div className="metric">
      <div className="k">{k}</div>
      <div className="v">
        {on === undefined ? v : <span className={`pill ${on ? "on" : "off"}`}>{v}</span>}
      </div>
    </div>
  );
}
