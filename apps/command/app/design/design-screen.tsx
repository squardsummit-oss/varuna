"use client";

import { PageHeader } from "@/components/varuna/page-header";

import { ColourSection } from "./_sections/colour-section";
import { ComponentsSection } from "./_sections/components-section";
import { MotionSection } from "./_sections/motion-section";
import { RampsSection } from "./_sections/ramps-section";
import { DESIGN_SECTIONS } from "./_sections/section";
import { ShapeSection } from "./_sections/shape-section";
import { TypeSection } from "./_sections/type-section";

/**
 * Internal design system page (SPEC.md 7.12, P0.8): every token, type size and component in each
 * of its states, plus the contrast report. This page is the visual regression baseline, so it is
 * built from the same components the console uses, never from copies.
 */
export function DesignScreen() {
  return (
    <div className="min-h-dvh bg-ink text-text">
      <div className="mx-auto flex w-full max-w-[1400px] flex-col gap-section px-6 py-8">
        <PageHeader
          title="Design system"
          description="Tokens, type, shape, components and motion, rendered from packages/tokens and components/varuna. Screens are checked against this page before they are marked done."
          honesty="Internal page: not part of the demo path"
        />

        <div className="flex flex-col gap-8 lg:flex-row lg:items-start lg:gap-10">
          <nav
            aria-label="Design system sections"
            className="lg:sticky lg:top-6 lg:w-56 lg:shrink-0"
          >
            <p className="type-micro mb-2 font-medium text-text-3">On this page</p>
            <ul className="flex flex-wrap gap-1 lg:flex-col">
              {DESIGN_SECTIONS.map((section) => (
                <li key={section.id}>
                  <a
                    href={`#${section.id}`}
                    className="type-small block rounded-control border border-line px-3 py-2 text-text-2 hover:bg-well hover:text-text focus-visible:ring-2 focus-visible:ring-tide focus-visible:outline-none"
                  >
                    {section.label}
                  </a>
                </li>
              ))}
            </ul>
          </nav>

          <main className="flex min-w-0 flex-1 flex-col gap-12">
            <ColourSection />
            <RampsSection />
            <TypeSection />
            <ShapeSection />
            <ComponentsSection />
            <MotionSection />
          </main>
        </div>
      </div>
    </div>
  );
}
