"use client";

import Shell from "@/components/Shell";
import SettingsForm from "@/components/SettingsForm";

export default function AiPage() {
  return (
    <Shell>
      <SettingsForm group="ai" title="AI Settings" />
    </Shell>
  );
}
