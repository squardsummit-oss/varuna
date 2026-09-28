import type { Metadata } from "next";

import { DrainsScreen } from "./drains-screen";

export const metadata: Metadata = {
  title: "Nadi (drain health)",
  description:
    "The learned blockage map, the observations that taught it, and the desilting priority list for Mumbai.",
};

export default function DrainsPage() {
  return <DrainsScreen />;
}
