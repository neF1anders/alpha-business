/**
 * Runtime configuration. Everything can be overridden from the URL:
 *   ?api=http&base=http://localhost:8000&company=<id>
 * or from `window.APP_CONFIG` set before `main.js` loads.
 */
const params = new URLSearchParams(window.location.search);
const preset = window.APP_CONFIG ?? {};

export const config = Object.freeze({
  /** "mock" — in-browser fake backend, "http" — real BFF from api-contract.md. */
  apiMode: params.get("api") ?? preset.apiMode ?? "mock",
  /** BFF origin; empty string means same origin. */
  apiBase: params.get("base") ?? preset.apiBase ?? "",
  /** The only demo client; there is no auth (see interface/api-contract.md). */
  companyId: params.get("company") ?? preset.companyId ?? "demo-company",
  /** Fallback polling when SSE is unavailable. */
  pollIntervalMs: preset.pollIntervalMs ?? 45_000,
});
