"use client";

import Shell from "@/components/Shell";
import SettingsForm from "@/components/SettingsForm";

export default function RiskPage() {
  return (
    <Shell>
      <SettingsForm group="risk" title="Risk Settings" />
    </Shell>
  );
}
