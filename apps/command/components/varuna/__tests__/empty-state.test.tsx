import { render, screen } from "@testing-library/react";
import { CloudRain } from "lucide-react";
import { describe, expect, it } from "vitest";

import { EmptyState } from "@/components/varuna/empty-state";

describe("EmptyState", () => {
  it("renders as a status region with the title and the fix", () => {
    render(
      <EmptyState
        title="No runs yet"
        description="Press Play on the replay, or Compute live."
        icon={CloudRain}
        action={<button type="button">Compute live</button>}
      />,
    );
    const region = screen.getByRole("status");
    expect(region).toHaveTextContent("No runs yet");
    expect(region).toHaveTextContent("Press Play on the replay, or Compute live.");
    expect(screen.getByRole("button", { name: "Compute live" })).toBeInTheDocument();
    expect(region.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });

  it("works with only a title", () => {
    render(<EmptyState title="No alerts in this window" size="sm" />);
    expect(screen.getByRole("status")).toHaveTextContent("No alerts in this window");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
