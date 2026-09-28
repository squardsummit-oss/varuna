/**
 * The citizen dashboard is its own shell: no icon rail, no top bar, no right rail of the console.
 *
 * It is read on a phone at 390 x 844 by somebody deciding whether to leave the house, so the map
 * owns the whole viewport and the rail is a sheet over it (UI_SPEC 3). `AppShell` is an operator's
 * chrome and is deliberately not mounted here, which is the same choice `/map` already makes.
 */
export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return <div className="bg-ink flex h-dvh min-h-0 flex-col">{children}</div>;
}
