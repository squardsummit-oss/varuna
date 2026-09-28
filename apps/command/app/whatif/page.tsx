import type { Metadata } from "next";

import { WhatIfScreen } from "./whatif-screen";

export const metadata: Metadata = {
  title: "Kalpana (what-if lab)",
  description:
    "Ask the twin a question and get the answer before the next radar frame: rain scale, cleaned pipes and a pump plan on the reduced-order emulator, and a tide offset on the full-city Twin.",
};

export default function WhatIfPage() {
  return <WhatIfScreen />;
}
