"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import Link from "next/link";
import { clearToken, getToken } from "@/lib/api";

const NAV = [
  { href: "/", label: "Main Dashboard" },
  { href: "/ideas", label: "AI Trade Ideas" },
  { href: "/trades", label: "Open Trades" },
  { href: "/risk", label: "Risk Settings" },
  { href: "/strategy", label: "Strategy Settings" },
  { href: "/ai", label: "AI Settings" },
  { href: "/ai-comparison", label: "AI Comparison" },
  { href: "/broker", label: "Capital.com" },
  { href: "/backtest", label: "Backtest" },
  { href: "/logs", label: "Logs / Audit" },
  { href: "/emergency", label: "Emergency Panel" },
];

export default function Shell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    setReady(true);
  }, [router]);

  if (!ready) return <div className="center">Loading…</div>;

  function logout() {
    clearToken();
    router.replace("/login");
  }

  return (
    <div className="layout">
      <aside className="sidebar">
        <h1>AI Trading System</h1>
        <p className="sub">Capital.com · risk-gated</p>
        <nav className="nav">
          {NAV.map((n) => (
            <Link key={n.href} href={n.href} className={pathname === n.href ? "active" : ""}>
              {n.label}
            </Link>
          ))}
        </nav>
        <button className="secondary" style={{ width: "100%", marginTop: 18 }} onClick={logout}>
          Log out
        </button>
      </aside>
      <main className="content">{children}</main>
    </div>
  );
}
