"use client";

import { Component, type ErrorInfo, type ReactNode } from "react";
import { RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Panel } from "@/components/varuna/panel";
import { errorMessage } from "@/lib/api/client";

export interface PanelErrorBoundaryProps {
  children: ReactNode;
  /** Name of the panel shown in the fallback header, e.g. "Hotspots". */
  title?: string;
  /** Called after the boundary resets; refetch or reset state here. */
  onRetry?: () => void;
}

interface PanelErrorBoundaryState {
  error: Error | null;
}

/**
 * Per-panel error boundary (SPEC.md section 7.13): a broken panel never blanks the map or the
 * page. The fallback keeps the panel's footprint, says what happened and offers a reload.
 */
export class PanelErrorBoundary extends Component<PanelErrorBoundaryProps, PanelErrorBoundaryState> {
  state: PanelErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): PanelErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error(`Panel "${this.props.title ?? "untitled"}" failed to render.`, error, info.componentStack);
  }

  private reset = (): void => {
    this.setState({ error: null });
    this.props.onRetry?.();
  };

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <Panel title={this.props.title} className="h-full" dense>
        <div role="alert" className="flex flex-col items-start gap-2">
          <p className="text-body font-medium text-text">This panel failed to render.</p>
          <p className="max-w-[44ch] text-small text-text-2">
            {errorMessage(error, "The rest of the screen keeps working.")} Reload the panel to try
            again; the map and the other panels are unaffected.
          </p>
          <Button variant="outline" size="sm" onClick={this.reset}>
            <RotateCcw data-icon="inline-start" aria-hidden="true" />
            Reload the panel
          </Button>
        </div>
      </Panel>
    );
  }
}
