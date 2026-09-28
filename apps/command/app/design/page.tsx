import type { Metadata } from "next";

import { DesignScreen } from "./design-screen";

export const metadata: Metadata = {
  title: "Design system",
  description:
    "Every VARUNA token, type size and component in each of its states, with the contrast report. The visual regression baseline for the console.",
};

export default function DesignPage() {
  return <DesignScreen />;
}
