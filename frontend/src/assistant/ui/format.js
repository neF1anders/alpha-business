const MINUTE = 60_000;
const HOUR = 60 * MINUTE;

/** "ещё 36 ч" / "ещё 20 мин" / "истекает" for `expires_at`. */
export function formatExpiresIn(expiresAt, now = Date.now()) {
  const left = new Date(expiresAt).getTime() - now;
  if (!Number.isFinite(left)) return "";
  if (left <= MINUTE) return "истекает";
  if (left < HOUR) return `ещё ${Math.round(left / MINUTE)} мин`;
  return `ещё ${Math.round(left / HOUR)} ч`;
}
