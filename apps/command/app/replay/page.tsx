import type { Metadata } from "next";

import { ReplayScreen } from "./replay-screen";

export const metadata: Metadata = {
  title: "Smriti (replay)",
  description:
    "Stream a stored storm, reconstructed or designed, through the live pipeline: bundle selection, the replay clock, the cycle log and the storm designer.",
};

export default function ReplayPage() {
  return <ReplayScreen />;
}
