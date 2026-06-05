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
  if (!res.ok) {
    throw new Error("Invalid username or password");
  }
  const data = await res.json();
  setToken(data.access_token);
}

export interface DashboardStatus {
  bot_running: boolean;
  execution_mode: string;
  auto_mode_enabled: boolean;
  hedging_enabled: boolean;
  broker_connected: boolean;
  account_balance: number | null;
  available_funds: number | null;
  today_pl: number | null;
  weekly_pl: number | null;
  open_trades_count: number;
  max_active_trades: number;
  current_open_risk: number | null;
  daily_loss_limit_used: number | null;
  last_ai_decision: string | null;
  last_risk_rejection_reason: string | null;
}

export async function getDashboardStatus(): Promise<DashboardStatus> {
  const token = getToken();
  const res = await fetch(`${API_BASE}/api/dashboard/status`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (res.status === 401) {
    clearToken();
    throw new Error("unauthorized");
  }
  if (!res.ok) throw new Error("failed to load status");
  return res.json();
}
