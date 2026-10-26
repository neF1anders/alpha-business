/**
 * Runtime configuration. Everything can be overridden from the URL:
 *   ?api=http&base=http://localhost:8000&company=<id>&role=<role>
 * or from `window.APP_CONFIG` set before `main.js` loads.
 * Company and role resolve as: URL params > demo session (login.html) > defaults.
 * Backend interface: docs/api-contract.md.
 */
import { loadSession } from "./auth/session.js";

const params = new URLSearchParams(window.location.search);
const preset = window.APP_CONFIG ?? {};
const session = loadSession();

const explicitCompanyId = params.get("company") ?? preset.companyId ?? null;
const companyId = explicitCompanyId ?? session?.companyId ?? "demo-company";
// A stored role belongs to the stored company only.
const sessionRole = session?.companyId === companyId ? session.role : null;

export const config = Object.freeze({
  /** "mock" — in-browser fake backend, "http" — real BFF from api-contract.md. */
  apiMode: params.get("api") ?? preset.apiMode ?? "mock",
  /** BFF origin; empty string means same origin. */
  apiBase: params.get("base") ?? preset.apiBase ?? "",
  companyId,
  /** Employee role (owner|ceo|cfo|treasurer|chief_accountant|procurement|sales) or null. */
  role: params.get("role") ?? preset.role ?? sessionRole ?? null,
  /** Without a session or an explicit `?company=` the app sends the visitor to the login page. */
  requiresLogin: explicitCompanyId === null && session === null,
  /** Fallback polling when SSE is unavailable. */
  pollIntervalMs: preset.pollIntervalMs ?? 45_000,
});
