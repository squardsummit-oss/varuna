import type { Metadata } from "next";

import { OnboardScreen } from "./onboard-screen";

export const metadata: Metadata = {
  title: "Pravesh (city onboarding)",
  description: "Onboard a city from open data and see its first street flood forecast.",
};

export default function OnboardPage() {
  return <OnboardScreen />;
}
