import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const motionPref = vi.hoisted(() => ({ reduced: false }));
vi.mock("motion/react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("motion/react")>();
  return { ...actual, useReducedMotion: () => motionPref.reduced };
});

import { AlertCard, type AlertSummary } from "@/components/varuna/alert-card";

const ALERT: AlertSummary = {
  id: "VARUNA-MUM-20190702T0310Z-SKY1.0-TWIN1.0-FLASH0.1-BAKED-STREET-0926-SEVERE",
  level: "severe",
  headline: "Sant Shitolebaba Maharaj Marg: depth likely above 45 cm from 08:40 to 09:40",
  area: "Sant Shitolebaba Maharaj Marg",
  triggerProbability: 0.8,
  raisedAt: "2019-07-02T08:40:00+05:30",
  persistsCycles: 4,
  persistsUnit: "forecast step",
  channels: ["Dashboard", "WhatsApp mock"],
};

function card(): HTMLElement {
  return screen.getByRole("article");
}

beforeEach(() => {
  motionPref.reduced = false;
});

describe("AlertCard motion M16", () => {
  it("slides a new card in from 12 px above, starting transparent", () => {
    render(<AlertCard alert={ALERT} entering />);
    expect(card()).toHaveAttribute("data-entering", "true");
    expect(card().style.opacity).toBe("0");
    expect(card().style.transform).toContain("translateY(-12px)");
  });

  it("only fades a new card in under reduced motion", () => {
    motionPref.reduced = true;
    render(<AlertCard alert={ALERT} entering />);
    expect(card().style.opacity).toBe("0");
    expect(card().style.transform).not.toContain("translateY");
  });

  it("renders a card already in the queue at rest", () => {
    render(<AlertCard alert={ALERT} />);
    expect(card()).not.toHaveAttribute("data-entering");
    expect(card().style.opacity).not.toBe("0");
    expect(card().style.transform).not.toContain("translateY(-12px)");
  });

  it("keeps the queue's copy and actions", () => {
    render(<AlertCard alert={ALERT} entering />);
    expect(screen.getByText(ALERT.headline)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Acknowledge" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Escalate" })).toBeInTheDocument();
  });
});
