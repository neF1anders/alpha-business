/**
 * Entry of login.html: mock Alfa ID sign-in plus one-click demo accounts.
 * Separate from the cabinet bundle so the shell and assistant are never loaded before sign-in.
 */
import { createLoginPage } from "./login-page.js";
import { saveSession } from "../auth/session.js";
import { authenticate, normalizePhone } from "../auth/directory.js";
import { withPassthroughParams } from "../auth/context.js";
import { replaceChildren } from "../ui/dom.js";

const CABINET_PAGE = "index.html";

function signIn({ personPhone, companyId, role }) {
  saveSession({ personPhone: normalizePhone(personPhone), companyId, role });
  window.location.replace(withPassthroughParams(CABINET_PAGE));
}

function signInWithCredentials(phone, password) {
  const membership = authenticate(phone, password);
  if (!membership) return false;
  signIn({ personPhone: phone, companyId: membership.company_id, role: membership.role });
  return true;
}

replaceChildren(document.getElementById("login"), createLoginPage({ signInWithCredentials, signIn }));
