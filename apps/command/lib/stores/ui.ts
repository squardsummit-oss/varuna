"use client";

import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

/**
 * Right-rail tabs on the console (SPEC.md section 7.2). The Alerts and Pumps mirrors were
 * removed at the team's request: /alerts and /pumps own those screens, and the rail repeated them.
 */
export const RIGHT_RAIL_TABS = ["hotspots", "reachability"] as const;
export type RightRailTab = (typeof RIGHT_RAIL_TABS)[number];

/** Vehicle profiles used by routing and safe-until (SPEC.md section 11.8). */
export const VEHICLE_PROFILES = [
  "two-wheeler",
  "car",
  "bus",
  "ambulance",
  "fire-tender",
  "pedestrian",
] as const;
export type VehicleProfile = (typeof VEHICLE_PROFILES)[number];

export const PROFILE_LABELS: Record<VehicleProfile, string> = {
  "two-wheeler": "Two-wheeler",
  car: "Car",
  bus: "Bus",
  ambulance: "Ambulance",
  "fire-tender": "Fire tender",
  pedestrian: "Pedestrian",
};

/** Default risk tolerance per profile: P(impassable) above which a segment is avoided. */
export const DEFAULT_RISK_TOLERANCE: Record<VehicleProfile, number> = {
  "two-wheeler": 0.5,
  car: 0.5,
  bus: 0.5,
  ambulance: 0.2,
  "fire-tender": 0.3,
  pedestrian: 0.5,
};

export interface UiState {
  commandPaletteOpen: boolean;
  shortcutsOpen: boolean;
  settingsOpen: boolean;
  layerPanelOpen: boolean;
  replayPanelOpen: boolean;
  rightRailTab: RightRailTab;
  /** Phone-mock sound; the only persisted value. */
  soundOn: boolean;
  riskTolerance: Record<VehicleProfile, number>;
  replaySpeedDefault: 1 | 10 | 30 | 60;

  setCommandPaletteOpen: (open: boolean) => void;
  toggleCommandPalette: () => void;
  setShortcutsOpen: (open: boolean) => void;
  toggleShortcuts: () => void;
  setSettingsOpen: (open: boolean) => void;
  toggleSettings: () => void;
  setLayerPanelOpen: (open: boolean) => void;
  toggleLayerPanel: () => void;
  setReplayPanelOpen: (open: boolean) => void;
  toggleReplayPanel: () => void;
  setRightRailTab: (tab: RightRailTab) => void;
  setSoundOn: (on: boolean) => void;
  toggleSound: () => void;
  setRiskTolerance: (profile: VehicleProfile, value: number) => void;
  setReplaySpeedDefault: (speed: 1 | 10 | 30 | 60) => void;
  /** Closes every overlay; used by Escape and by navigation. */
  closeOverlays: () => void;
}

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      commandPaletteOpen: false,
      shortcutsOpen: false,
      settingsOpen: false,
      layerPanelOpen: true,
      replayPanelOpen: true,
      rightRailTab: "hotspots",
      soundOn: false,
      riskTolerance: { ...DEFAULT_RISK_TOLERANCE },
      replaySpeedDefault: 30,

      setCommandPaletteOpen: (open) => set({ commandPaletteOpen: open }),
      toggleCommandPalette: () => set((s) => ({ commandPaletteOpen: !s.commandPaletteOpen })),
      setShortcutsOpen: (open) => set({ shortcutsOpen: open }),
      toggleShortcuts: () => set((s) => ({ shortcutsOpen: !s.shortcutsOpen })),
      setSettingsOpen: (open) => set({ settingsOpen: open }),
      toggleSettings: () => set((s) => ({ settingsOpen: !s.settingsOpen })),
      setLayerPanelOpen: (open) => set({ layerPanelOpen: open }),
      toggleLayerPanel: () => set((s) => ({ layerPanelOpen: !s.layerPanelOpen })),
      setReplayPanelOpen: (open) => set({ replayPanelOpen: open }),
      toggleReplayPanel: () => set((s) => ({ replayPanelOpen: !s.replayPanelOpen })),
      setRightRailTab: (tab) => set({ rightRailTab: tab }),
      setSoundOn: (on) => set({ soundOn: on }),
      toggleSound: () => set((s) => ({ soundOn: !s.soundOn })),
      setRiskTolerance: (profile, value) =>
        set((s) => ({ riskTolerance: { ...s.riskTolerance, [profile]: clamp01(value) } })),
      setReplaySpeedDefault: (speed) => set({ replaySpeedDefault: speed }),
      closeOverlays: () =>
        set({ commandPaletteOpen: false, shortcutsOpen: false, settingsOpen: false }),
    }),
    {
      name: "varuna.ui",
      storage: createJSONStorage(() => localStorage),
      partialize: (s) => ({ soundOn: s.soundOn }),
      // Rehydrated from Providers after mount so server and first client render agree.
      skipHydration: true,
    },
  ),
);

/** True when any modal overlay owned by the ui store is open. */
export const selectAnyOverlayOpen = (s: UiState): boolean =>
  s.commandPaletteOpen || s.shortcutsOpen || s.settingsOpen;
