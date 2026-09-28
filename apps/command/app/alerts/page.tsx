import type { Metadata } from "next";

import { AlertsScreen } from "./alerts-screen";

export const metadata: Metadata = {
  title: "Sanket (alert centre)",
  description:
    "Every alert VARUNA raises, the CAP 1.2 document it sends, and the WhatsApp message the ward officer receives.",
};

export default function AlertsPage() {
  return <AlertsScreen />;
}
