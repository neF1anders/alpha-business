import { config } from "../config.js";
import { findCompany, findEmployee, membershipsOf } from "./directory.js";
import { clearSession, loadSession, saveSession } from "./session.js";

export const LOGIN_PAGE = "login.html";

/** Everything the cabinet needs to know about who is signed in. Pure reads, no DOM. */
export function currentContext() {
  const session = loadSession();
  const company = findCompany(config.companyId);
  const employee = company && config.role ? findEmployee(company.company_id, config.role) : null;
  const memberships = session ? membershipsOf(session.personPhone) : [];
  return { session, company, employee, memberships };
}

export function switchCompany(companyId) {
  const { session, memberships } = currentContext();
  const membership = memberships.find((item) => item.company_id === companyId);
  if (!session || !membership) return;
  saveSession({ ...session, companyId, role: membership.role });
  reloadWithoutOverrides();
}

export function switchEmployee(role) {
  const { session } = currentContext();
  if (!session) return;
  saveSession({ ...session, role });
  reloadWithoutOverrides();
}

export function signOut() {
  clearSession();
  window.location.replace(withPassthroughParams(LOGIN_PAGE));
}

/** Keeps `api` and `base` so a demo started against the real BFF stays on it. */
export function withPassthroughParams(target) {
  const incoming = new URLSearchParams(window.location.search);
  const outgoing = new URLSearchParams();
  for (const key of ["api", "base"]) if (incoming.has(key)) outgoing.set(key, incoming.get(key));
  const query = outgoing.toString();
  return query ? `${target}?${query}` : target;
}

/** URL `company`/`role` would beat the new session, so they are dropped before reload. */
function reloadWithoutOverrides() {
  const url = new URL(window.location.href);
  const hadOverrides = url.searchParams.has("company") || url.searchParams.has("role");
  url.searchParams.delete("company");
  url.searchParams.delete("role");
  if (hadOverrides) window.location.replace(url);
  else window.location.reload();
}
