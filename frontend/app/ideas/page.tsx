"use client";

import { useCallback, useEffect, useState } from "react";
import Shell from "@/components/Shell";
import { approveIdea, listIdeas, rejectIdea, scanNow, type Idea } from "@/lib/api";

export default function IdeasPage() {
  const [ideas, setIdeas] = useState<Idea[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    listIdeas().then(setIdeas).catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    refresh();
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

  return (
    <Shell>
      <div className="row spread">
        <h2 className="page-title">AI Trade Ideas</h2>
        <button className="sm" disabled={busy} onClick={() => act(scanNow)}>
          Scan now
        </button>
      </div>
      {error && <div className="banner">{error}</div>}
      {ideas.length === 0 && <p className="muted">No ideas yet. Run a scan to generate proposals.</p>}

      {ideas.map((i) => (
        <div key={i.id} className={`idea-card ${i.risk_approved ? "approved" : "rejected"}`}>
          <div className="row spread">
            <div className="row">
              <strong>{i.instrument}</strong>
              <span className={i.direction === "long" ? "long" : i.direction === "short" ? "short" : "muted"}>
                {i.direction.toUpperCase()}
              </span>
              <span className="tag">{i.strategy}</span>
              {i.market_classification && <span className="tag">{i.market_classification}</span>}
              <span className="tag">{i.status}</span>
            </div>
            <span className={i.risk_approved ? "pos" : "neg"}>
              {i.risk_approved ? "Risk approved" : "Rejected"}
            </span>
          </div>

          <div className="kv">
            <div><div className="k">Entry</div><div className="v">{round(i.entry_price)}</div></div>
            <div><div className="k">Stop-loss</div><div className="v">{round(i.stop_loss)}</div></div>
            <div><div className="k">TP1</div><div className="v">{round(i.take_profit_1)}</div></div>
            <div><div className="k">TP2</div><div className="v">{round(i.take_profit_2)}</div></div>
            <div><div className="k">Risk/Reward</div><div className="v">{i.risk_reward}</div></div>
            <div><div className="k">Confidence</div><div className="v">{i.confidence}%</div></div>
            <div><div className="k">Size</div><div className="v">{round(i.position_size, 4)}</div></div>
            <div><div className="k">Risk (AED)</div><div className="v">{round(i.risk_aed)}</div></div>
          </div>

          {i.rationale && <p className="muted" style={{ marginTop: 0 }}>{i.rationale}</p>}
          {i.risk_flags?.length > 0 && (
            <div className="row" style={{ marginTop: 6 }}>
              {i.risk_flags.map((f, idx) => (
                <span key={idx} className="tag">{f}</span>
              ))}
            </div>
          )}
          {!i.risk_approved && <p className="neg" style={{ fontSize: 13 }}>{i.risk_reason}</p>}

          {i.status === "approved" && i.risk_approved && (
            <div className="row" style={{ marginTop: 10 }}>
              <button className="sm" disabled={busy} onClick={() => act(() => approveIdea(i.id))}>
                Approve &amp; Execute
              </button>
              <button className="secondary sm" disabled={busy} onClick={() => act(() => rejectIdea(i.id))}>
                Reject
              </button>
            </div>
          )}
        </div>
      ))}
    </Shell>
  );
}

function round(v: number, dp = 2): string {
  if (v === null || v === undefined) return "—";
  return (+v).toLocaleString(undefined, { maximumFractionDigits: dp });
}
