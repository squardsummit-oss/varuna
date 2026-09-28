/**
 * `GET /rural` - the low-bandwidth road advisory (UI_SPEC 7, PRD 3.3, task D-16).
 *
 * A route handler rather than a `page.tsx`, because the requirement is a hard one: no client
 * JavaScript and under 30 KB transferred. Every page under `app/layout.tsx` inherits the app
 * shell - `globals.css`, the Bricolage and Geist webfonts, and the React runtime that hydrates
 * it - and a route handler is the only way to render a document outside it. The cost and the
 * measurement are written up in `app/rural/html.ts`.
 *
 * The page is never cached: it quotes a run and a departure time, and a cached advisory is a
 * wrong one the moment the cycle moves.
 */

import { buildPage } from "./data";
import { renderRural } from "./html";

export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const { page, status } = await buildPage(url, url.origin);
  return new Response(renderRural(page), {
    status,
    headers: {
      "content-type": "text/html; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}
