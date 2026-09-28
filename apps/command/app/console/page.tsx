import type { Metadata } from "next";

import { ConsoleScreen } from "./console-screen";

export const metadata: Metadata = {
  title: "Drishti (command console)",
  description:
    "VARUNA command console: street-by-street flood depth for the next three hours, replayed or live.",
};

export default function ConsolePage() {
  return <ConsoleScreen />;
}
