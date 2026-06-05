"use client";

import Shell from "@/components/Shell";
import SettingsForm from "@/components/SettingsForm";

export default function StrategyPage() {
  return (
    <Shell>
      <SettingsForm group="strategy" title="Strategy Settings" />
    </Shell>
  );
}
