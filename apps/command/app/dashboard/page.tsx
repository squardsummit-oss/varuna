import type { Metadata } from "next";

import { DashboardScreen } from "./dashboard-screen";

export const metadata: Metadata = {
  title: "Citizen dashboard",
  description:
    "Which streets near you are passable, and the safe way to where you are going. VARUNA's street-level flood forecast on a map you already know.",
};

export default function DashboardPage() {
  return <DashboardScreen />;
}
