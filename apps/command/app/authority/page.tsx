import type { Metadata } from "next";

import { AuthorityScreen } from "./authority-screen";
import { WardMap } from "./ward-map";

export const metadata: Metadata = {
  title: "Ward officer's desk",
  description:
    "Close a street, withhold a pump, dispatch the plan and acknowledge an alert. Every edit is appended to VARUNA's ops log and applied when a route is read.",
  // The desk is not a public page: it takes a shared passphrase and shows an operational log.
  robots: { index: false, follow: false },
};

export default function AuthorityPage() {
  // The ward map is passed in rather than imported by the screen, so the screen stays a client
  // component with one job and the map - which pulls `FloodMap`, deck.gl and a run load behind
  // it - is a child this server component hands over. It must be framed on `ENTRY_AOI` from its
  // first paint or motion M27's cross-fade lands somewhere the globe was not; `WardMap` is.
  return <AuthorityScreen wardMap={<WardMap />} />;
}
