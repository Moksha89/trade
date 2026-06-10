// API client for the dashboard.
//
// Calls are made relative to the current origin so that Nginx can proxy
// `/api/*` and `/health` to the FastAPI backend. Override with
// NEXT_PUBLIC_API_BASE for local development against a separate port.

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";
const TOKEN_KEY = "ats_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}
export function setToken(token: string): void {
  window.localStorage.setItem(TOKEN_KEY, token);
}
export function clearToken(): void {
  window.localStorage.removeItem(TOKEN_KEY);
}

export async function login(username: string, password: string): Promise<void> {
  const body = new URLSearchParams({ username, password });
  const res = await fetch(`${API_BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!res.ok) throw new Error("Invalid username or password");
  const data = await res.json();
  setToken(data.access_token);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    ...(init.headers as Record<string, string>),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (init.body) headers["Content-Type"] = "application/json";
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (res.status === 401) {
    clearToken();
    throw new Error("unauthorized");
  }
  if (!res.ok) {
    let detail = `request failed (${res.status})`;
    try {
      const j = await res.json();
      if (j.detail) detail = j.detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const apiGet = <T>(p: string) => request<T>(p);
export const apiPost = <T>(p: string, body?: unknown) =>
  request<T>(p, { method: "POST", body: body ? JSON.stringify(body) : undefined });
export const apiPut = <T>(p: string, body: unknown) =>
  request<T>(p, { method: "PUT", body: JSON.stringify(body) });

// ---- types ----
export interface DashboardStatus {
  bot_running: boolean;
  execution_mode: string;
  auto_mode_enabled: boolean;
  hedging_enabled: boolean;
  broker_connected: boolean;
  trading_locked: boolean;
  lock_reason: string | null;
  account_balance: number | null;
  account_equity: number | null;
  open_pnl: number | null;
  available_funds: number | null;
  today_pl: number | null;
  weekly_pl: number | null;
  open_trades_count: number;
  max_active_trades: number;
  current_open_risk: number | null;
  max_combined_open_risk: number;
  daily_loss_limit: number;
  daily_loss_limit_used: number | null;
  last_ai_decision: string | null;
  last_risk_rejection_reason: string | null;
  last_heartbeat: string | null;
}

export interface Idea {
  id: number;
  created_at: string;
  instrument: string;
  direction: string;
  strategy: string;
  entry_price: number;
  stop_loss: number;
  take_profit_1: number;
  take_profit_2: number;
  confidence: number;
  risk_reward: number;
  position_size: number;
  risk_aed: number;
  rationale: string;
  risk_flags: string[];
  market_classification: string | null;
  risk_approved: boolean;
  risk_reason: string;
  status: string;
}

export interface Trade {
  id: number;
  mode: string;
  instrument: string;
  direction: string;
  strategy: string;
  entry_price: number;
  current_price: number;
  size: number;
  stop_loss: number;
  take_profit_1: number;
  take_profit_2: number;
  r_multiple: number;
  initial_risk_aed: number;
  unrealized_pl: number;
  realized_pl: number;
  status: string;
  breakeven_moved: boolean;
  profit_locked: boolean;
  partial_closed: boolean;
  opened_at: string;
  closed_at: string | null;
  close_reason: string | null;
}

export interface AuditLog {
  id: number;
  created_at: string;
  event: string;
  instrument: string | null;
  detail: Record<string, unknown>;
}

export interface BacktestReport {
  instrument: string;
  trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  profit_factor: number;
  avg_r: number;
  max_drawdown_r: number;
  best_trade_r: number;
  worst_trade_r: number;
  total_r: number;
  by_strategy: Record<string, Record<string, number>>;
}

// ---- endpoints ----
export const getDashboardStatus = () => apiGet<DashboardStatus>("/api/dashboard/status");
export const startBot = () => apiPost("/api/bot/start");
export const stopBot = () => apiPost("/api/bot/stop");
export const scanNow = () => apiPost<{ created: Idea[] }>("/api/bot/scan");
export const listIdeas = () => apiGet<Idea[]>("/api/ideas");
export const approveIdea = (id: number) => apiPost(`/api/ideas/${id}/approve`);
export const rejectIdea = (id: number) => apiPost(`/api/ideas/${id}/reject`);
export const listTrades = (status?: string) =>
  apiGet<Trade[]>(`/api/trades${status ? `?status=${status}` : ""}`);
export const closeTrade = (id: number) => apiPost(`/api/trades/${id}/close`);
export const moveSL = (id: number, stop_loss: number) =>
  apiPost(`/api/trades/${id}/move-sl`, { stop_loss });
export const moveTP = (id: number, take_profit: number) =>
  apiPost(`/api/trades/${id}/move-tp`, { take_profit });
export const getSettings = (g: string) => apiGet<Record<string, unknown>>(`/api/settings/${g}`);
export const putSettings = (g: string, patch: Record<string, unknown>) =>
  apiPut<Record<string, unknown>>(`/api/settings/${g}`, patch);
export const listLogs = (limit = 200) => apiGet<AuditLog[]>(`/api/logs?limit=${limit}`);
export const runBacktest = (instrument: string, bars = 600) =>
  apiPost<BacktestReport>("/api/backtest", { instrument, bars });
export const emergency = (action: string) => apiPost(`/api/emergency/${action}`);
export const brokerStatus = () => apiGet<Record<string, unknown>>("/api/broker/status");
export const brokerReconnect = () => apiPost("/api/broker/reconnect");

// ---- AI shadow comparison (Claude vs local Ollama) ----
export interface ShadowSide {
  direction: string;
  strategy: string;
  confidence: number;
  risk_reward: number;
  entry: number;
  stop_loss: number;
  take_profit_1: number;
  latency_ms: number;
  model?: string;
  error?: string | null;
}
export interface ShadowRow {
  id: number;
  created_at: string | null;
  instrument: string;
  market_classification: string | null;
  agree: boolean;
  claude: ShadowSide;
  ollama: ShadowSide;
}
export interface ShadowSummary {
  total: number;
  comparable: number;
  errors: number;
  agree: number;
  agreement_rate: number;
  both_trade: number;
  both_no_trade: number;
  claude_trade_only: number;
  ollama_trade_only: number;
  avg_claude_latency_ms: number;
  avg_ollama_latency_ms: number;
  avg_claude_confidence: number;
  avg_ollama_confidence: number;
}
export interface AIComparison {
  summary: ShadowSummary;
  recent: ShadowRow[];
  enabled: boolean;
  shadow_model: string;
  ollama: { reachable: boolean; version?: string; error?: string };
}
export const getAIComparison = (limit = 100) =>
  apiGet<AIComparison>(`/api/ai-comparison?limit=${limit}`);
export const setShadowEnabled = (enabled: boolean) =>
  putSettings("ai", { shadow_compare_enabled: enabled });
