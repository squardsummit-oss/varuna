/**
 * `WS /v1/live` client (SPEC.md section 11.11).
 *
 * One socket per tab, shared by every subscriber through a small event bus. The connection
 * opens when the first subscriber appears and closes when the last one leaves. Reconnects use
 * exponential backoff with jitter (500 ms to 30 s). The API sends `{ "type": "ping" }` every
 * 20 s; the client answers `pong` and treats 50 s of silence as a dead link.
 */
"use client";

import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";

import { wsUrl } from "./client";
import { LiveEvent, type LiveTopic } from "./schemas";

export type LiveState = "connecting" | "open" | "closed";

export type LiveHandler = (event: LiveEvent) => void;

/** `"*"` receives every event, including heartbeats. */
export type LiveTopicFilter = LiveTopic | (string & {}) | "*";

export const HEARTBEAT_INTERVAL_MS = 20_000;
export const HEARTBEAT_TIMEOUT_MS = 50_000;
export const RECONNECT_MIN_MS = 500;
export const RECONNECT_MAX_MS = 30_000;

interface Subscription {
  topics: ReadonlySet<string>;
  handler: LiveHandler;
}

interface Snapshot {
  state: LiveState;
  attempts: number;
  lastEvent: LiveEvent | null;
  lastEventAt: number | null;
  url: string;
}

type Listener = () => void;

/**
 * The bus is intentionally tiny: `subscribe` returns an unsubscribe function, `emit` fans out.
 * It is exported so tests and non-React code (the command palette) can use it directly.
 */
export class LiveBus {
  private readonly subscriptions = new Set<Subscription>();

  subscribe(topics: readonly LiveTopicFilter[] | "*", handler: LiveHandler): () => void {
    const set = new Set<string>(topics === "*" ? ["*"] : topics);
    const subscription: Subscription = { topics: set, handler };
    this.subscriptions.add(subscription);
    return () => {
      this.subscriptions.delete(subscription);
    };
  }

  emit(event: LiveEvent): void {
    for (const subscription of this.subscriptions) {
      if (subscription.topics.has("*") || subscription.topics.has(event.type)) {
        try {
          subscription.handler(event);
        } catch (error) {
          // One broken subscriber must never stop the others (error boundary per panel).
          console.error("live subscriber failed", error);
        }
      }
    }
  }

  get size(): number {
    return this.subscriptions.size;
  }
}

/** Manages the socket lifecycle; one instance per URL lives for the tab. */
export class LiveConnection {
  readonly bus = new LiveBus();
  private socket: WebSocket | null = null;
  private snapshot: Snapshot;
  private readonly listeners = new Set<Listener>();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private watchdogTimer: ReturnType<typeof setTimeout> | null = null;
  private refs = 0;
  private wantOpen = false;

  constructor(readonly url: string) {
    this.snapshot = { state: "closed", attempts: 0, lastEvent: null, lastEventAt: null, url };
  }

  getSnapshot = (): Snapshot => this.snapshot;

  listen(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  /** Reference-counted: the socket opens on the first retain and closes on the last release. */
  retain(): () => void {
    this.refs += 1;
    if (this.refs === 1) {
      this.wantOpen = true;
      this.connect();
    }
    let released = false;
    return () => {
      if (released) return;
      released = true;
      this.refs = Math.max(0, this.refs - 1);
      if (this.refs === 0) this.shutdown();
    };
  }

  /** Sends a JSON message when the socket is open; returns false otherwise. */
  send(message: unknown): boolean {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(typeof message === "string" ? message : JSON.stringify(message));
      return true;
    }
    return false;
  }

  /** Drops the current socket and reconnects immediately (attempt counter reset). */
  reconnect(): void {
    if (!this.wantOpen) return;
    this.update({ attempts: 0 });
    this.closeSocket();
    this.connect();
  }

  private update(patch: Partial<Snapshot>): void {
    this.snapshot = { ...this.snapshot, ...patch };
    for (const listener of this.listeners) listener();
  }

  private connect(): void {
    if (typeof WebSocket === "undefined" || !this.wantOpen) return;
    if (this.socket && this.socket.readyState <= WebSocket.OPEN) return;
    this.update({ state: "connecting" });
    let socket: WebSocket;
    try {
      socket = new WebSocket(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;

    socket.onopen = () => {
      if (this.socket !== socket) return;
      this.update({ state: "open", attempts: 0 });
      this.armWatchdog();
    };

    socket.onmessage = (message: MessageEvent<unknown>) => {
      if (this.socket !== socket) return;
      this.armWatchdog();
      const event = parseLiveEvent(message.data);
      if (!event) return;
      if (event.type === "ping") {
        this.send({ type: "pong", ts: new Date().toISOString() });
      }
      this.update({ lastEvent: event, lastEventAt: Date.now() });
      this.bus.emit(event);
    };

    socket.onerror = () => {
      // `onclose` always follows; the reconnect is scheduled there.
    };

    socket.onclose = () => {
      if (this.socket !== socket) return;
      this.socket = null;
      this.clearWatchdog();
      this.update({ state: "closed" });
      this.scheduleReconnect();
    };
  }

  private scheduleReconnect(): void {
    if (!this.wantOpen || this.reconnectTimer) return;
    const attempts = this.snapshot.attempts;
    const base = Math.min(RECONNECT_MAX_MS, RECONNECT_MIN_MS * 2 ** attempts);
    const jitter = base * (0.8 + Math.random() * 0.4);
    this.update({ attempts: attempts + 1 });
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, jitter);
  }

  private armWatchdog(): void {
    this.clearWatchdog();
    this.watchdogTimer = setTimeout(() => {
      // No ping within the timeout: the link is dead even if the OS has not noticed.
      this.closeSocket();
      this.scheduleReconnect();
    }, HEARTBEAT_TIMEOUT_MS);
  }

  private clearWatchdog(): void {
    if (this.watchdogTimer) {
      clearTimeout(this.watchdogTimer);
      this.watchdogTimer = null;
    }
  }

  private closeSocket(): void {
    const socket = this.socket;
    this.socket = null;
    this.clearWatchdog();
    if (socket) {
      socket.onmessage = null;
      socket.onerror = null;
      socket.onclose = null;
      try {
        if (socket.readyState === WebSocket.CONNECTING) {
          // Closing a socket that has not finished its handshake makes the browser log
          // "WebSocket is closed before the connection is established" - a warning the app
          // caused and section 14 counts ("zero console errors/warnings during the Playwright
          // demo run"). It happens on every console mount in development, because React
          // mounts an effect twice and the first cleanup lands mid-handshake. Waiting for the
          // handshake and closing then is silent, and the socket is already detached from
          // every handler above, so nothing it delivers in the meantime reaches the app.
          socket.onopen = () => socket.close();
        } else {
          socket.onopen = null;
          socket.close();
        }
      } catch {
        // Already closed.
      }
    }
    if (this.snapshot.state !== "closed") this.update({ state: "closed" });
  }

  private shutdown(): void {
    this.wantOpen = false;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.closeSocket();
    this.update({ attempts: 0 });
  }
}

/**
 * Parses a socket frame; ignores anything that is not a `{ type }` or `{ topic }` object.
 *
 * The API sends the bus envelope, whose discriminator is `topic` (SPEC.md section 11.11);
 * the console has always read `type`, and the handshake and ping frames use `type`. Both are
 * accepted and normalised to `type`, so a subscriber only ever matches on one field.
 */
export function parseLiveEvent(raw: unknown): LiveEvent | null {
  if (typeof raw !== "string") return null;
  let frame: unknown;
  try {
    frame = JSON.parse(raw);
  } catch {
    return null;
  }
  if (frame && typeof frame === "object" && !("type" in frame) && "topic" in frame) {
    const { topic, ...rest } = frame as { topic: unknown };
    if (typeof topic === "string") frame = { ...rest, type: topic, topic };
  }
  const parsed = LiveEvent.safeParse(frame);
  return parsed.success ? parsed.data : null;
}

const connections = new Map<string, LiveConnection>();

/** The shared connection for a URL (default `wsUrl()`); created lazily. */
export function getLiveConnection(url: string = wsUrl()): LiveConnection {
  let connection = connections.get(url);
  if (!connection) {
    connection = new LiveConnection(url);
    connections.set(url, connection);
  }
  return connection;
}

export interface UseLiveOptions {
  /** Called for every event whose `type` is in `topics` (or all events with `"*"`). */
  onEvent?: LiveHandler;
  /** Topics to listen for; defaults to all product events (never heartbeats). */
  topics?: readonly LiveTopicFilter[] | "*";
  /** Set false to stay disconnected (server rendering, hidden panels). */
  enabled?: boolean;
  /** Override the socket URL (tests). */
  url?: string;
}

export interface UseLiveResult {
  state: LiveState;
  /** Reconnect attempts since the last successful open. */
  attempts: number;
  lastEvent: LiveEvent | null;
  lastEventAt: number | null;
  send: (message: unknown) => boolean;
  reconnect: () => void;
}

const DEFAULT_TOPICS: readonly LiveTopicFilter[] = [
  "runs.published",
  "cycle.stage",
  "alert.raised",
  "alert.updated",
  "alert.cleared",
  "obs.assimilated",
  "replay.clock",
  "onboard.progress",
];

const SERVER_SNAPSHOT: Snapshot = {
  state: "closed",
  attempts: 0,
  lastEvent: null,
  lastEventAt: null,
  url: "",
};

/**
 * Subscribes this component to the live socket. The handler is kept in a ref so re-renders
 * never re-subscribe; the topic list is compared by value.
 */
export function useLive(options: UseLiveOptions = {}): UseLiveResult {
  const { onEvent, topics = DEFAULT_TOPICS, enabled = true, url } = options;
  const handlerRef = useRef<LiveHandler | undefined>(onEvent);
  useEffect(() => {
    handlerRef.current = onEvent;
  }, [onEvent]);

  const topicKey = topics === "*" ? "*" : [...topics].sort().join("|");
  const resolvedUrl = url ?? (typeof window === "undefined" ? "" : wsUrl());
  const connection = resolvedUrl ? getLiveConnection(resolvedUrl) : null;

  useEffect(() => {
    if (!enabled || !connection) return;
    const release = connection.retain();
    const unsubscribe = connection.bus.subscribe(
      topicKey === "*" ? "*" : topicKey.split("|").filter(Boolean),
      (event) => handlerRef.current?.(event),
    );
    return () => {
      unsubscribe();
      release();
    };
  }, [connection, enabled, topicKey]);

  const subscribe = useCallback(
    (listener: Listener) => (connection ? connection.listen(listener) : () => {}),
    [connection],
  );
  const getSnapshot = useCallback(
    () => (connection ? connection.getSnapshot() : SERVER_SNAPSHOT),
    [connection],
  );
  const snapshot = useSyncExternalStore(subscribe, getSnapshot, () => SERVER_SNAPSHOT);

  const send = useCallback((message: unknown) => connection?.send(message) ?? false, [connection]);
  const reconnect = useCallback(() => connection?.reconnect(), [connection]);

  return {
    state: enabled ? snapshot.state : "closed",
    attempts: snapshot.attempts,
    lastEvent: snapshot.lastEvent,
    lastEventAt: snapshot.lastEventAt,
    send,
    reconnect,
  };
}
