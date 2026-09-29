import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { PageHeader } from "@/components/varuna/page-header";
import { navItem } from "@/lib/nav";

function renderHeader(props: Partial<React.ComponentProps<typeof PageHeader>> = {}) {
  const item = navItem("alerts");
  return render(
    <TooltipProvider>
      <PageHeader title={item.label} screen={item} {...props} />
    </TooltipProvider>,
  );
}

describe("PageHeader", () => {
  it("keeps the Sanskrit title, the English gloss and the Devanagari on screen", () => {
    renderHeader();
    const item = navItem("alerts");
    expect(screen.getByRole("heading", { level: 1, name: item.label })).toBeInTheDocument();
    expect(screen.getByText(item.gloss ?? "")).toBeVisible();
    expect(screen.getByText(item.deva ?? "")).toHaveAttribute("lang", "sa");
  });

  it("gives the word's meaning as a focusable tooltip and screen-reader text, not a paragraph", () => {
    renderHeader();
    const item = navItem("alerts");
    const meaning = screen.getByText(item.meaning ?? "");
    // The only copy of the meaning in the page is the visually hidden one inside the gloss line;
    // the visible text is the tooltip that opens on hover or focus.
    expect(meaning).toHaveClass("sr-only");
    const gloss = meaning.closest("p");
    expect(gloss).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("heading", { level: 1 })).toHaveAttribute(
      "aria-describedby",
      gloss?.id,
    );
  });

  it("prints a one-line description and the honesty chip when given", () => {
    renderHeader({ description: "Ward officer to public.", honesty: "Exercise" });
    expect(screen.getByText("Ward officer to public.")).toBeVisible();
    expect(screen.getByText("Exercise")).toBeVisible();
  });

  it("renders a plain title when the screen has no gloss", () => {
    render(<PageHeader title="Report water" description="Where you are." />);
    expect(screen.getByRole("heading", { level: 1, name: "Report water" })).not.toHaveAttribute(
      "aria-describedby",
    );
    expect(screen.getByText("Where you are.")).toBeVisible();
  });
});
