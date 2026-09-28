import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  RouteForm,
  defaultRouteRequest,
  withDepartureTime,
  withProfile,
  type RouteRequest,
} from "@/components/varuna/route-form";
import { formatPct } from "@/lib/format";
import { DEFAULT_RISK_TOLERANCE } from "@/lib/stores/ui";

/** The page owns the request; this harness plays that part so the form stays controlled. */
function Harness() {
  const [request, setRequest] = useState<RouteRequest>(() =>
    defaultRouteRequest("2019-07-02T06:40:00+05:30"),
  );
  return <RouteForm value={request} onChange={setRequest} disabled />;
}

describe("RouteForm", () => {
  it("presets the demo trip: KEM Hospital to Sion Hospital by ambulance", () => {
    render(<Harness />);
    expect(screen.getByLabelText("Origin")).toHaveValue("kem-hospital");
    expect(screen.getByLabelText("Destination")).toHaveValue("sion-hospital");
    expect(screen.getByLabelText("Vehicle profile")).toHaveValue("ambulance");
    expect(screen.getByText("20 %")).toBeInTheDocument();
  });

  it("resets the risk tolerance to the profile default when the profile changes", () => {
    render(<Harness />);
    const profile = screen.getByLabelText("Vehicle profile");

    fireEvent.change(profile, { target: { value: "car" } });
    expect(profile).toHaveValue("car");
    expect(screen.getByText(formatPct(DEFAULT_RISK_TOLERANCE.car))).toBeInTheDocument();

    fireEvent.change(profile, { target: { value: "ambulance" } });
    expect(profile).toHaveValue("ambulance");
    expect(screen.getByText(formatPct(DEFAULT_RISK_TOLERANCE.ambulance))).toBeInTheDocument();

    fireEvent.change(profile, { target: { value: "car" } });
    expect(screen.getByText("50 %")).toBeInTheDocument();
  });

  it("keeps 'Find route' disabled with the reason it is disabled", () => {
    render(<Harness />);
    const button = screen.getByRole("button", { name: "Find route" });
    expect(button).toBeDisabled();
    expect(
      screen.getByText("Routing lands in Phase 8; the form is live so the demo trip is preset"),
    ).toBeInTheDocument();
  });
});

describe("withProfile", () => {
  it("carries the profile default tolerance, not the one set for another vehicle", () => {
    const base = defaultRouteRequest("2019-07-02T06:40:00+05:30");
    const car = withProfile({ ...base, riskTolerance: 0.85 }, "car");
    expect(car.riskTolerance).toBe(DEFAULT_RISK_TOLERANCE.car);
    expect(withProfile(car, "ambulance").riskTolerance).toBe(DEFAULT_RISK_TOLERANCE.ambulance);
  });
});

describe("withDepartureTime", () => {
  it("replaces the clock and keeps the date and the +05:30 offset", () => {
    expect(withDepartureTime("2019-07-02T06:40:00+05:30", "08:20")).toBe(
      "2019-07-02T08:20:00+05:30",
    );
  });

  it("ignores an incomplete time so the request never holds an invalid instant", () => {
    expect(withDepartureTime("2019-07-02T06:40:00+05:30", "18:")).toBe(
      "2019-07-02T06:40:00+05:30",
    );
  });
});
