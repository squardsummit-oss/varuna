import type { Metadata } from "next";

import { RouteScreen } from "./route-screen";

export const metadata: Metadata = {
  title: "Marga (route planner)",
  description:
    "Flood-safe routing for Mumbai: an ambulance from KEM Hospital to Sion Hospital, around the streets VARUNA expects to be impassable.",
};

export default function RoutePage() {
  return <RouteScreen />;
}
