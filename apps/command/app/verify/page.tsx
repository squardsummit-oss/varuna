import type { Metadata } from "next";

import { VerifyScreen } from "./verify-screen";

export const metadata: Metadata = {
  title: "Pramana (verification)",
  description:
    "How VARUNA scores itself: skill against sourced ground truth, reliability, skill by lead time, and the limitations we state before anyone asks.",
};

export default function VerifyPage() {
  return <VerifyScreen />;
}
