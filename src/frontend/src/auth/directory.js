import { DEMO_COMPANIES, DEMO_PASSWORD } from "./demo-accounts.js";

/** Phone digits only, so "+7 903 221-90-46" and "89032219046" are the same login. */
export function normalizePhone(value) {
  const digits = String(value ?? "").replace(/\D/g, "");
  if (digits.length === 11 && digits.startsWith("8")) return `7${digits.slice(1)}`;
  return digits;
}

export function findCompany(companyId) {
  return DEMO_COMPANIES.find((company) => company.company_id === companyId) ?? null;
}

export function findEmployee(companyId, role) {
  return findCompany(companyId)?.roles.find((employee) => employee.role === role) ?? null;
}

/** Person = unique phone; a person may work in several companies (holding owner). */
export function membershipsOf(phone) {
  const wanted = normalizePhone(phone);
  if (!wanted) return [];
  return DEMO_COMPANIES.flatMap((company) =>
    company.roles
      .filter((employee) => normalizePhone(employee.phone) === wanted)
      .map((employee) => ({ company_id: company.company_id, role: employee.role })),
  );
}

/**
 * Returns the first membership of the person or null for unknown phone / wrong password.
 * Both failures look the same to the caller, like in a real bank.
 */
export function authenticate(phone, password) {
  if (password !== DEMO_PASSWORD) return null;
  return membershipsOf(phone)[0] ?? null;
}

export function formatRubles(value) {
  return `${new Intl.NumberFormat("ru-RU").format(Math.round(value))} ₽`;
}
