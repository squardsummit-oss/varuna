"use client";

import { Panel } from "@/components/varuna/panel";
import { BrandLockup, Wordmark, WordmarkMark } from "@/components/varuna/wordmark";

import { Demo } from "./section";

const WORDMARK_SIZES = [
  { size: "sm", note: "The top bar, the public map, the report flow, the 404 and the error page." },
  { size: "md", note: "Panels." },
  { size: "lg", note: "Large headers." },
] as const;

/**
 * The brand, from the team's own logo (`public/brand/`, derived by `tools/brand_assets.py`):
 * the wordmark at its three sizes, the emblem alone, and the full lockup the landing page carries.
 * There is no loading or error state to show - the files are static and the box is reserved.
 */
export function BrandStories() {
  return (
    <Panel
      title="Wordmark and brand"
      description="The emblem and VARUNA in display type, the one word set in capitals. The emblem carries the name for assistive technology; the lettering beside it is hidden from it, so a screen reader says the name once."
    >
      <div className="flex flex-col gap-6">
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          {WORDMARK_SIZES.map(({ size, note }) => (
            <Demo key={size} label={`Wordmark, ${size}`} note={note}>
              <Wordmark size={size} withMark />
            </Demo>
          ))}
        </div>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <Demo label="Wordmark without the emblem" note="The 404 and the error page.">
            <Wordmark size="sm" withMark={false} />
          </Demo>
          <Demo
            label="Emblem alone"
            note="16, 20, 32 and 64 px: the phone mock draws it at 16 and 20 beside its own text, so there it is decorative."
          >
            <div className="flex items-end gap-4">
              <WordmarkMark size={16} alt="" />
              <WordmarkMark size={20} alt="" />
              <WordmarkMark size={32} />
              <WordmarkMark size={64} />
            </div>
          </Demo>
          <Demo
            label="Lockup"
            note="The emblem over the lettered name, as the landing hero draws it at 200 px."
          >
            <BrandLockup width={200} />
          </Demo>
        </div>
      </div>
    </Panel>
  );
}
