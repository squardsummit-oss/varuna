"use client";

import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { formatPct, formatSpeed } from "@/lib/format";
import { isTheme, useTheme } from "@/lib/theme";
import { isReplaySpeed, REPLAY_SPEEDS } from "@/lib/stores/replay";
import { PROFILE_LABELS, useUiStore, VEHICLE_PROFILES } from "@/lib/stores/ui";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <h3 className="type-small text-text-2 font-medium">{title}</h3>
      {children}
    </section>
  );
}

/** Settings sheet (SPEC.md section 7.13). Every change applies immediately; there is no save. */
export function SettingsDrawer() {
  const open = useUiStore((s) => s.settingsOpen);
  const setOpen = useUiStore((s) => s.setSettingsOpen);
  const soundOn = useUiStore((s) => s.soundOn);
  const setSoundOn = useUiStore((s) => s.setSoundOn);
  const riskTolerance = useUiStore((s) => s.riskTolerance);
  const setRiskTolerance = useUiStore((s) => s.setRiskTolerance);
  const replaySpeedDefault = useUiStore((s) => s.replaySpeedDefault);
  const setReplaySpeedDefault = useUiStore((s) => s.setReplaySpeedDefault);
  const { theme, setTheme } = useTheme();

  return (
    <Sheet open={open} onOpenChange={(next) => setOpen(next)}>
      <SheetContent side="right" className="motion-reduce:transition-none">
        <SheetHeader>
          <SheetTitle>Settings</SheetTitle>
          <SheetDescription>Changes apply immediately.</SheetDescription>
        </SheetHeader>

        <div className="flex flex-col gap-6 overflow-y-auto px-4 pb-4">
          <Section title="Sound">
            <div className="flex items-center justify-between gap-4">
              <Label htmlFor="settings-sound" className="type-body text-text font-normal">
                Phone mock sound
              </Label>
              <Switch
                id="settings-sound"
                checked={soundOn}
                onCheckedChange={(checked) => setSoundOn(checked)}
              />
            </div>
            <p className="type-small text-text-3">
              A short chime when new alerts reach the phone mock in the alert centre. Off by
              default; switch it on for the demo. The browser lets it play after your next click or
              key press, and it stays silent when your system asks for reduced motion.
            </p>
          </Section>

          <Separator />

          <Section title="Units">
            <div className="flex items-center justify-between gap-4">
              <Label htmlFor="settings-units" className="type-body text-text font-normal">
                Depth is shown in centimetres
              </Label>
              <Switch
                id="settings-units"
                checked
                disabled
                aria-label="Depth unit, locked to centimetres"
              />
            </div>
            <p className="type-small text-text-3">
              Operators, alerts and the CAP feed all use centimetres, so one unit avoids misread
              depths.
            </p>
          </Section>

          <Separator />

          <Section title="Risk tolerance">
            <p className="type-small text-text-3">
              A segment is avoided when its chance of being impassable is above the tolerance.
            </p>
            <ul className="flex flex-col gap-4">
              {VEHICLE_PROFILES.map((profile) => {
                const value = riskTolerance[profile];
                const id = `settings-risk-${profile}`;
                return (
                  <li key={profile} className="flex flex-col gap-2">
                    <div className="flex items-center justify-between gap-4">
                      <Label htmlFor={id} className="type-body text-text font-normal">
                        {PROFILE_LABELS[profile]}
                      </Label>
                      <span className="num type-small text-text-2">{formatPct(value)}</span>
                    </div>
                    <Slider
                      id={id}
                      aria-label={`${PROFILE_LABELS[profile]} risk tolerance`}
                      min={0}
                      max={1}
                      step={0.05}
                      value={[value]}
                      onValueChange={(next) => {
                        const first = Array.isArray(next) ? next[0] : next;
                        if (typeof first === "number") setRiskTolerance(profile, first);
                      }}
                    />
                  </li>
                );
              })}
            </ul>
          </Section>

          <Separator />

          <Section title="Replay speed default">
            <ToggleGroup
              aria-label="Replay speed default"
              variant="outline"
              value={[String(replaySpeedDefault)]}
              onValueChange={(values) => {
                const next = Number(values[0]);
                if (isReplaySpeed(next)) setReplaySpeedDefault(next);
              }}
            >
              {REPLAY_SPEEDS.map((speed) => (
                <ToggleGroupItem key={speed} value={String(speed)} className="num">
                  {formatSpeed(speed)}
                </ToggleGroupItem>
              ))}
            </ToggleGroup>
            <p className="type-small text-text-3">
              Speed the replay starts at when a bundle loads.
            </p>
          </Section>

          <Separator />

          <Section title="Theme">
            <ToggleGroup
              aria-label="Theme"
              variant="outline"
              value={[theme]}
              onValueChange={(values) => {
                const next = values[0];
                if (isTheme(next)) setTheme(next);
              }}
            >
              <ToggleGroupItem value="dark">Dark</ToggleGroupItem>
              <ToggleGroupItem value="light">Light</ToggleGroupItem>
            </ToggleGroup>
            <p className="type-small text-text-3">Dark for a control room, light for daylight.</p>
          </Section>
        </div>
      </SheetContent>
    </Sheet>
  );
}
