import type { Metadata } from "next";

import { ReportScreen } from "./report-screen";

export const metadata: Metadata = {
  title: "Report water",
  description:
    "Tell VARUNA where the water is: a location, an optional photo and one of three depths. Reports reach Pulse in the next five-minute cycle.",
};

export default function ReportPage() {
  return <ReportScreen />;
}
