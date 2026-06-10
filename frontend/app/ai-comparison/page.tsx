"use client";

import { useCallback, useEffect, useState } from "react";
import Shell from "@/components/Shell";
import {
  getAIComparison,
  setShadowEnabled,
  type AIComparison,
  type ShadowRow,
  type ShadowSide,
} from "@/lib/api";

export default function AIComparisonPage() {
  const [data, setData] = useState<AIComparison | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    getAIComparison(150)
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e) => setError((e as Error).message));
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 10000);
    return () => clearInterval(t);
  }, [refresh]);

  async function toggle() {
    if (!data) return;
    setBusy(true);
    setError(null);
    try {
      await setShadowEnabled(!data.enabled);
      refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const s = data?.summary;
  const ollamaOk = data?.ollama?.reachable;
  // Distinguish "still connecting to the API" from a genuine "shadow off /
  // ollama offline" state, so a slow/restarting backend never looks like
  // everything is switched off.
  const loaded = data !== null;

  return (
    <Shell>
      <div className="row spread">
        <h2 className="page-title">AI Comparison — Claude vs Local (Ollama)</h2>
        <div className="row">
          {!loaded ? (
            <span className="pill">Connecting…</span>
          ) : (
            <>
              <span className={`pill ${ollamaOk ? "on" : "off"}`}>
                Ollama {ollamaOk ? `online${data?.ollama?.version ? " v" + data.ollama.version : ""}` : "offline"}
              </span>
              <button className={data?.enabled ? "secondary sm" : "sm"} disabled={busy} onClick={toggle}>
                {data?.enabled ? "Shadow: ON (click to pause)" : "Shadow: OFF (click to enable)"}
              </button>
            </>
          )}
        </div>
      </div>

      <p className="muted" style={{ marginTop: 4, maxWidth: 760 }}>
        Every scan, the local model analyses the <strong>same</strong> market data as Claude. It never
        places a trade — these rows just measure whether the free local model would make the same calls,
        so we can decide with evidence whether to switch and cut the API bill. Model: <code>{data?.shadow_model}</code>.
      </p>

      {error && <div className="banner">{error}</div>}

      {s && (
        <>
          <div className="grid" style={{ marginTop: 16 }}>
            <Metric k="Decisions compared" v={`${s.comparable}`} />
            <Metric k="Agreement rate" v={`${s.agreement_rate}%`} cls={agreeCls(s.agreement_rate)} />
            <Metric k="Agreed" v={`${s.agree}`} />
            <Metric k="Ollama errors" v={`${s.errors}`} cls={s.errors ? "neg" : undefined} />
            <Metric k="Both: trade" v={`${s.both_trade}`} />
            <Metric k="Both: no-trade" v={`${s.both_no_trade}`} />
            <Metric k="Claude trades, Ollama skips" v={`${s.claude_trade_only}`} />
            <Metric k="Ollama trades, Claude skips" v={`${s.ollama_trade_only}`} />
            <Metric k="Avg latency — Claude" v={`${fmtMs(s.avg_claude_latency_ms)}`} />
            <Metric k="Avg latency — Ollama" v={`${fmtMs(s.avg_ollama_latency_ms)}`} cls={s.avg_ollama_latency_ms > s.avg_claude_latency_ms ? "neg" : "pos"} />
            <Metric k="Avg conf — Claude (trades)" v={`${s.avg_claude_confidence}`} />
            <Metric k="Avg conf — Ollama (trades)" v={`${s.avg_ollama_confidence}`} />
          </div>

          <div className="card" style={{ marginTop: 16 }}>
            <strong>Verdict so far:</strong>{" "}
            {s.comparable === 0 ? (
              <span className="muted">No comparisons recorded yet. Enable shadow mode and wait for the next scan.</span>
            ) : (
              <span>
                The local model agreed with Claude on <strong>{s.agreement_rate}%</strong> of{" "}
                {s.comparable} decisions. It diverged when Claude traded but Ollama skipped{" "}
                <strong>{s.claude_trade_only}</strong>× and when Ollama traded but Claude skipped{" "}
                <strong>{s.ollama_trade_only}</strong>×. Local inference is{" "}
                {s.avg_claude_latency_ms > 0
                  ? `${(s.avg_ollama_latency_ms / Math.max(s.avg_claude_latency_ms, 1)).toFixed(1)}×`
                  : "—"}{" "}
                the latency of the API but costs $0 per call.
              </span>
            )}
          </div>
        </>
      )}

      <div className="card" style={{ marginTop: 16 }}>
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Instrument</th>
              <th>Market</th>
              <th>Claude</th>
              <th>Ollama</th>
              <th>Match</th>
              <th>Latency C / O</th>
            </tr>
          </thead>
          <tbody>
            {(!data || data.recent.length === 0) && (
              <tr>
                <td colSpan={7} className="muted">
                  {!loaded ? "Connecting to the API…" : "No comparisons yet."}
                </td>
              </tr>
            )}
            {data?.recent.map((r) => (
              <Row key={r.id} r={r} />
            ))}
          </tbody>
        </table>
      </div>
    </Shell>
  );
}

function Row({ r }: { r: ShadowRow }) {
  return (
    <tr>
      <td className="muted">{r.created_at?.replace("T", " ").slice(5, 16)}</td>
      <td>{r.instrument}</td>
      <td className="muted">{r.market_classification ?? "—"}</td>
      <td>{decision(r.claude)}</td>
      <td>{r.ollama.error ? <span className="neg" title={r.ollama.error}>error</span> : decision(r.ollama)}</td>
      <td>
        {r.ollama.error ? (
          <span className="muted">—</span>
        ) : (
          <span className={`pill ${r.agree ? "on" : "off"}`}>{r.agree ? "agree" : "differ"}</span>
        )}
      </td>
      <td className="muted">
        {fmtMs(r.claude.latency_ms)} / {fmtMs(r.ollama.latency_ms)}
      </td>
    </tr>
  );
}

function decision(d: ShadowSide) {
  const dir = d.direction;
  const cls = dir === "long" ? "long" : dir === "short" ? "short" : "muted";
  if (dir === "no_trade") return <span className="muted">no-trade</span>;
  return (
    <span>
      <span className={cls}>{dir}</span>{" "}
      <span className="muted">
        {d.strategy} · conf {d.confidence} · RR {d.risk_reward}
      </span>
    </span>
  );
}

function agreeCls(pct: number): string | undefined {
  if (pct >= 75) return "pos";
  if (pct < 50) return "neg";
  return undefined;
}

function fmtMs(ms: number): string {
  if (!ms) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

function Metric({ k, v, cls }: { k: string; v: string; cls?: string }) {
  return (
    <div className="metric">
      <div className="k">{k}</div>
      <div className="v">
        <span className={cls}>{v}</span>
      </div>
    </div>
  );
}
