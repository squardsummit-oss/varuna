/**
 * Copy the loaders.gl decoder workers this app needs into `public/workers/`, so no decode ever
 * reaches a CDN.
 *
 * **What this is for.** loaders.gl resolves a decoder worker to
 * `https://unpkg.com/@loaders.gl/<module>@<version>/dist/<id>-worker.js` unless the application
 * names a `workerUrl` per loader - read out of
 * `worker-utils/dist/lib/worker-api/get-worker-url.js`, not assumed. SPEC.md 17 requires the
 * finale to run with the venue's network off, so a decoder fetched from unpkg at run time is not
 * an option, and `components/map/layers/photoreal.ts` had been decoding on the main thread to
 * avoid generating that URL at all. This script is what lets it stop: the two bundles land under
 * `public/workers/`, Next serves them from this origin, and `getWorkerURL` returns the local path
 * before it ever reaches its unpkg branch.
 *
 * **Which bundles, and how that list was arrived at.** Not from the docs: from the glTF parser.
 * `@loaders.gl/gltf/dist/lib/extensions/KHR_draco_mesh_compression.js` parses with `DracoLoader`
 * (worker id `draco`), and `dist/lib/parsers/parse-gltf.js:181` parses every glTF image with
 * `[ImageLoader, BasisLoader]` (worker id `basis`; `ImageLoader` has no worker and decodes through
 * the browser's own `createImageBitmap`). Nothing else on the 3D Tiles path declares
 * `worker: true` - `@loaders.gl/3d-tiles`, `@loaders.gl/tiles` and `@loaders.gl/images` ship no
 * worker bundle at all. So the list is exactly Draco and Basis.
 *
 * **What Google actually serves today, measured rather than assumed.** Walking the tileset from
 * the global root down to Hindmata junction (19.012 N, 72.841 E) on 2026-09-23 and sampling 24 of
 * the 216 glTF tiles on that path, across the whole range from geometric error 525,957 m to
 * 2.006 m: every one of the 24 declares `extensionsUsed: ["KHR_materials_unlit"]` and nothing
 * else, carries **no** `KHR_draco_mesh_compression` on any mesh primitive, and carries **no**
 * `KHR_texture_basisu` - its textures are `image/jpeg`. The `asset.generator` string is
 * `"draco_decoder"` on all 24, which is the name of Google's own server-side tool: it decodes
 * Draco before serving, it does not ship it. So on today's tiles neither of these workers is ever
 * spun up. They are shipped because the tileset's format is Google's to change and a
 * `KHR_draco_mesh_compression` tile arriving on a laptop with no network must still decode, not
 * because a decode was measured moving off the main thread.
 *
 * **What this still does not buy.** `draco-worker.js` carries
 * `https://www.gstatic.com/draco/versioned/decoders/<version>` inside it: wherever Draco runs, its
 * wasm comes from gstatic unless a bundled `draco3d` is passed through `options.modules`, and
 * `draco3d` is not a dependency of this app. That URL is only reached by a tile that is actually
 * Draco-compressed, and none of the 24 sampled is - but it is the reason this script cannot be
 * described as making 3D mode offline-capable. The tiles themselves come from
 * `tile.googleapis.com` and a photographed city needs a network by construction; SPEC.md 17's
 * offline finale is served by VARUNA's own map still being there.
 *
 * **Why these are generated and not committed.** They are byte-for-byte copies of files that
 * `pnpm install` already puts in `node_modules`, and a committed copy can silently outlive the
 * dependency it came from: bump `@loaders.gl/draco` and the stale bundle in `public/` keeps being
 * served, which is precisely the mismatch loaders.gl's own `validateWorkerVersion` exists to
 * complain about at run time. Generating them makes that impossible - this script refuses to copy
 * a bundle whose package version is not the one `package.json` pins - and an offline clone is no
 * worse off, because it has to run `pnpm install` before it can run Next at all. The cost is one
 * more thing a deploy has to run, which is why it is chained into `dev` and `build` rather than
 * left to a pre/post hook: pnpm's `enable-pre-post-scripts` is off by default, so a `predev`
 * script here would simply never have run.
 *
 * Idempotent on purpose: a bundle whose bytes already match is left alone, so repeated runs do not
 * churn mtimes and a rebuild is not invalidated by this script having been run twice.
 *
 * Usage: `node scripts/copy-loader-workers.mjs`, or `pnpm workers`, or `make workers`.
 */

import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const outDir = join(appRoot, "public", "workers");
const require = createRequire(join(appRoot, "package.json"));

/**
 * The worker bundles to copy, keyed by the loaders.gl worker id that names each one's `workerUrl`
 * option. The published file name is `<id>-worker.js` because that is the name `getWorkerURL`
 * builds for a browser, so the local path and the CDN path it replaces differ only in origin.
 */
const WORKERS = [
  { id: "draco", pkg: "@loaders.gl/draco", file: "draco-worker.js" },
  { id: "basis", pkg: "@loaders.gl/textures", file: "basis-worker.js" },
];

/**
 * The directory a package was installed into.
 *
 * Every `@loaders.gl` package declares a strict `exports` map with no `./package.json` entry, so
 * `require.resolve("@loaders.gl/draco/package.json")` throws `ERR_PACKAGE_PATH_NOT_EXPORTED` and
 * `require.resolve(pkg)` is the only way in. Walking up from the resolved entry point until a
 * `package.json` with the right `name` appears survives both pnpm's symlinked layout and a flat
 * `node_modules`, where hard-coding "two levels above dist/index.js" would not.
 */
function packageRoot(name) {
  let dir = dirname(require.resolve(name));
  for (let depth = 0; depth < 10; depth += 1) {
    const manifest = join(dir, "package.json");
    if (existsSync(manifest)) {
      const parsed = JSON.parse(readFileSync(manifest, "utf8"));
      if (parsed.name === name) return { dir, version: parsed.version };
    }
    const up = dirname(dir);
    if (up === dir) break;
    dir = up;
  }
  throw new Error(
    `Cannot find where ${name} is installed. Run pnpm install in apps/command and try again.`,
  );
}

/** The exact version `package.json` pins, so a copied bundle cannot outlive its package. */
function pinnedVersion(name) {
  const manifest = JSON.parse(readFileSync(join(appRoot, "package.json"), "utf8"));
  return manifest.dependencies?.[name] ?? manifest.devDependencies?.[name];
}

const sha256 = (buffer) => createHash("sha256").update(buffer).digest("hex");

function main() {
  mkdirSync(outDir, { recursive: true });
  const manifest = [];
  let rewritten = 0;

  for (const { id, pkg, file } of WORKERS) {
    const { dir, version } = packageRoot(pkg);
    const pinned = pinnedVersion(pkg);
    if (pinned && pinned !== version) {
      throw new Error(
        `${pkg} resolves to ${version} but package.json pins ${pinned}, so the bundle copied ` +
          `into public/workers/ would not match the loaders.gl this app imports. Run pnpm ` +
          `install in apps/command to line them up, then run this script again.`,
      );
    }

    const source = join(dir, "dist", file);
    if (!existsSync(source)) {
      throw new Error(
        `${pkg} ${version} has no dist/${file}, so there is no worker to serve for "${id}". ` +
          `Check what that version publishes before changing the list in ` +
          `scripts/copy-loader-workers.mjs.`,
      );
    }

    const bytes = readFileSync(source);
    const digest = sha256(bytes);
    const target = join(outDir, file);
    const unchanged = existsSync(target) && sha256(readFileSync(target)) === digest;
    if (!unchanged) {
      writeFileSync(target, bytes);
      rewritten += 1;
    }
    manifest.push({ id, package: pkg, version, file, bytes: bytes.length, sha256: digest });
    console.log(
      `${unchanged ? "up to date" : "copied   "}  /workers/${file}  ` +
        `${bytes.length} bytes  ${pkg}@${version}`,
    );
  }

  // A fixed key order and a trailing newline, so two runs against the same install write
  // identical bytes - the determinism rule SPEC.md 8 puts on `make bake`, applied here too.
  const manifestPath = join(outDir, "manifest.json");
  const next = `${JSON.stringify(
    { generatedBy: "scripts/copy-loader-workers.mjs", workers: manifest },
    null,
    2,
  )}\n`;
  if (!existsSync(manifestPath) || readFileSync(manifestPath, "utf8") !== next) {
    writeFileSync(manifestPath, next);
  }
  console.log(`${rewritten} of ${WORKERS.length} bundles rewritten; manifest at public/workers/`);
}

main();
