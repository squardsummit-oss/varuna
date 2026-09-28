/**
 * A plain-data picture of the deck.gl layers `CityMap` hands to deck, for the MO1 refactor's
 * equivalence tests.
 *
 * The refactor promises no visual change, and deck draws exactly what these props say. So the
 * promise is checked on the props: every layer's id and class in order, every literal prop, and
 * every `get*` accessor **evaluated on the layer's own data** - a colour function is compared by
 * the colours it returns, not by its source text. Functions that are not accessors (tile loaders,
 * sub-layer renderers) are recorded as present, which is all a diff of them could say.
 *
 * The fixture in `__fixtures__/monolith-layers.json` was written by this serializer from the
 * 1,133-line `city-map.tsx` before any code moved; the tests compare against it.
 */

type Plain = null | boolean | number | string | Plain[] | { [key: string]: Plain };

/** Stand-in for a decoded depth frame: jsdom has no ImageBitmap, and deck never reads it here. */
export function fakeBitmap(step: number): ImageBitmap {
  return { __fakeBitmap: step } as unknown as ImageBitmap;
}

function plain(value: unknown, depth = 0): Plain {
  if (depth > 8) return "[deep]";
  if (value === null || value === undefined) return null;
  if (typeof value === "number") {
    if (Number.isNaN(value)) return "NaN";
    if (!Number.isFinite(value)) return value > 0 ? "Infinity" : "-Infinity";
    return value;
  }
  if (typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "function") return "[function]";
  if (Array.isArray(value)) return value.map((item) => plain(item, depth + 1));
  if (typeof value === "object") {
    const record = value as Record<string, unknown>;
    if ("__fakeBitmap" in record) return `[bitmap ${String(record.__fakeBitmap)}]`;
    const name = (value as object).constructor?.name;
    if (name && name !== "Object") {
      // Extensions and interpolators: the class and the options they were built with.
      const opts = (record as { opts?: unknown }).opts;
      return { class: name, opts: plain(opts ?? null, depth + 1) };
    }
    const out: Record<string, Plain> = {};
    for (const key of Object.keys(record).sort()) out[key] = plain(record[key], depth + 1);
    return out;
  }
  return String(value);
}

interface LayerLike {
  id: string;
  constructor: { name: string };
  props: Record<string, unknown>;
}

/** The props a layer was constructed with (own keys only, not deck's defaults). */
function ownProps(layer: LayerLike): Record<string, unknown> {
  return layer.props;
}

export function serializeLayer(layer: unknown): Plain {
  const l = layer as LayerLike;
  const props = ownProps(l);
  const data = Array.isArray(props.data) ? (props.data as unknown[]) : null;
  const out: Record<string, Plain> = { id: l.id, class: l.constructor.name };
  // deck keeps async props (`data`, `image`) behind getters rather than as own keys, so they are
  // read by name: without this a frame swap or a changed data array would not show in the diff.
  out["async:data"] = data ? { length: data.length } : plain(props.data);
  if ("image" in props) out["async:image"] = plain(props.image);
  for (const key of Object.keys(props).sort()) {
    if (key === "id") continue;
    const value = props[key];
    if (key === "data") {
      out.data = data ? { length: data.length } : plain(value);
      continue;
    }
    if (typeof value === "function" && key.startsWith("get") && data) {
      const accessor = value as (d: unknown, info: unknown) => unknown;
      out[key] = data.map((d, index) => plain(accessor(d, { index, data, target: [] })));
      continue;
    }
    out[key] = plain(value);
  }
  return out;
}

export function serializeLayers(layers: readonly unknown[]): Plain[] {
  return layers.map(serializeLayer);
}
