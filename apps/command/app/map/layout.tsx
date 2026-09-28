import { PublicI18nProvider } from "@/lib/i18n/provider";

/**
 * The public map is its own shell: no icon rail, no top bar, no right rail. It is read on a
 * phone at 390 x 844 by a commuter, so the map owns the whole viewport (SPEC.md section 7.11).
 * It is also one of the two screens a citizen can read in Hindi or Marathi (task P9.9).
 */
export default function MapLayout({ children }: { children: React.ReactNode }) {
  return (
    <PublicI18nProvider>
      <div className="bg-ink flex h-dvh min-h-0 flex-col">{children}</div>
    </PublicI18nProvider>
  );
}
