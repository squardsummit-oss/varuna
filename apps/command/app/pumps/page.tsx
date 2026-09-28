import type { Metadata } from "next";

import { PumpsScreen } from "./pumps-screen";

export const metadata: Metadata = {
  title: "Jalayantra (pump dispatch)",
  description:
    "Assign dewatering pumps to the Mumbai hotspots that will peak first, and read the dispatch order in plain language.",
};

export default function PumpsPage() {
  return <PumpsScreen />;
}
