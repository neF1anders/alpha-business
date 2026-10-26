/**
 * Frontend view of backend DTOs (src/summary_branch/models.py).
 *
 * @typedef {"attention" | "watch" | "opportunity"} Severity
 * @typedef {"primary" | "bank" | "media"} SourceKind
 * @typedef {{kind: SourceKind, title: string, url: string, ref_id?: string}} SourceRef
 *
 * @typedef {object} BannerDTO
 * @property {string} banner_id
 * @property {Severity} severity
 * @property {string} label            e.g. "Внимание · ставка"
 * @property {string} headline
 * @property {string} for_you
 * @property {boolean} caveat
 * @property {boolean} raw_news
 * @property {SourceRef[]} sources
 * @property {string} expires_at       ISO 8601 UTC
 * @property {string} [inline_text]    requested from backend, see README
 *
 * @typedef {{attention: number, watch: number, opportunity: number, hidden?: number}} SignalCounts
 * @typedef {{counts: SignalCounts, items: BannerDTO[]}} SignalsResponse
 * @typedef {{item: BannerDTO | null, context?: {counterparty?: string}}} InlineSignalResponse
 *
 * @typedef {"fact" | "bank" | "model" | "hypothesis"} ClaimType
 * @typedef {{ref_id: string, quote?: string}} EvidenceRef
 * @typedef {{type: ClaimType, text: string, evidence: EvidenceRef[], grounding?: number | null}} Claim
 *
 * @typedef {object} ChatAnswer
 * @property {string} answer_id
 * @property {Claim[]} claims
 * @property {SourceRef[]} sources
 * @property {boolean} caveat
 * @property {boolean} raw_news
 * @property {string[]} follow_ups
 * @property {number} confidence
 *
 * @typedef {"payments" | "counterparties" | "new_payment"} InlinePage
 */

/** @type {Severity[]} */
export const SEVERITIES = ["attention", "watch", "opportunity"];

export const SEVERITY_TITLES = {
  attention: "Внимание",
  watch: "Наблюдения",
  opportunity: "Возможности",
};

/** Accessible names for the rail indicators. */
export const SEVERITY_INDICATOR_TITLES = {
  attention: "Предупреждения",
  watch: "Наблюдения",
  opportunity: "Возможности",
};

export const CLAIM_TYPE_TITLES = {
  fact: "Факт",
  bank: "Банк",
  model: "Оценка",
  hypothesis: "Гипотеза",
};

/** @param {BannerDTO[]} items */
export function countBySeverity(items) {
  const counts = { attention: 0, watch: 0, opportunity: 0 };
  for (const item of items) counts[item.severity] += 1;
  return counts;
}

/** Text for the inline strip: dedicated `inline_text`, otherwise headline + personal line. */
export function inlineTextOf(banner) {
  if (banner.inline_text) return banner.inline_text;
  return [banner.headline, banner.for_you].filter(Boolean).join(". ");
}
