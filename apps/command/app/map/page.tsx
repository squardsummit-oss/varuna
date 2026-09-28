import type { Metadata } from "next";

import { MapScreen } from "./map-screen";

export const metadata: Metadata = {
  title: "Public map",
  description:
    "Which streets near you are passable, and until when. Three colours, one vehicle, updated every five minutes from the last VARUNA run.",
};

export default function PublicMapPage() {
  return <MapScreen />;
}
