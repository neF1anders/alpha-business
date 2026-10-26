import { ApiError } from "./http-client.js";
import { countBySeverity } from "../assistant/model.js";
import { INLINE_PLACEMENTS, createInitialBanners, createPushQueue, pickAnswer } from "./mock-data.js";

/**
 * In-browser fake of the BFF. Same interface as the http client plus `demo`
 * controls (scenario switch, push, low-confidence chat) for the demo panel.
 *
 * Scenarios: "normal" | "empty" | "error" | "slow".
 */
const SETTINGS_KEY = "alfa-insights-mock";
const LATENCY_MS = { normal: 650, empty: 500, error: 700, slow: 3500 };
const CHAT_LATENCY_MS = 1400;

/** Short per-role angle appended to `for_you`, so switching the employee is visible in mock mode. */
const ROLE_ANGLE = {
  owner: "Для собственника: влияет на стоимость и устойчивость бизнеса.",
  ceo: "Для директора: учтите в планах на квартал.",
  cfo: "Для финансового директора: проверьте план движения денег.",
  treasurer: "Для казначея: следите за ликвидностью на счетах.",
  chief_accountant: "Для главбуха: проверьте учёт и сроки отчётности.",
  procurement: "Для закупок: пересмотрите условия с поставщиками.",
  sales: "Для продаж: обсудите условия с ключевыми покупателями.",
};

function personalise(banners, role) {
  const angle = ROLE_ANGLE[role];
  if (!angle) return banners;
  return banners.map((banner) => (banner.for_you ? { ...banner, for_you: `${banner.for_you} ${angle}` } : banner));
}

export function createMockClient({ role = null } = {}) {
  const settings = loadSettings();
  const seedBanners = () => personalise(createInitialBanners(), role);
  const seedPushQueue = () => personalise(createPushQueue(), role);
  let banners = seedBanners();
  let pushQueue = seedPushQueue();
  const hiddenIds = new Set();
  const streamListeners = new Set();

  const wait = (ms = LATENCY_MS[settings.scenario]) => new Promise((resolve) => setTimeout(resolve, ms));
  const visibleBanners = () => banners.filter((banner) => !hiddenIds.has(banner.banner_id));

  async function respond(producer, latency) {
    await wait(latency);
    if (settings.scenario === "error") throw new ApiError("unavailable", "Mock backend is in error scenario", 503);
    return structuredClone(producer());
  }

  const api = {
    getSignals: () =>
      respond(() => {
        const items = settings.scenario === "empty" ? [] : visibleBanners();
        return { counts: { ...countBySeverity(items), hidden: hiddenIds.size }, items };
      }),

    getInlineSignal: (page) =>
      respond(() => {
        const placement = INLINE_PLACEMENTS[page];
        const item = placement && visibleBanners().find((banner) => banner.banner_id === placement.bannerId);
        if (!item || settings.scenario === "empty") return { item: null };
        return { item, context: placement.context };
      }),

    markSeen: () => respond(() => null, 150),

    suppress: (bannerId) =>
      respond(() => {
        banners = banners.filter((banner) => banner.banner_id !== bannerId);
        return null;
      }, 250),

    dismiss: (bannerId) =>
      respond(() => {
        hiddenIds.add(bannerId);
        return null;
      }, 250),

    ask: ({ question, bannerId }) =>
      respond(() => {
        const template = pickAnswer({ question, bannerId, forceRawNews: settings.lowConfidenceChat });
        return { answer_id: `a-${Date.now()}`, raw_news: false, ...template };
      }, CHAT_LATENCY_MS),

    subscribe(handlers) {
      streamListeners.add(handlers);
      return () => streamListeners.delete(handlers);
    },
  };

  const demo = {
    getSettings: () => ({ ...settings }),
    setScenario(scenario) {
      settings.scenario = scenario;
      saveSettings(settings);
    },
    setLowConfidenceChat(enabled) {
      settings.lowConfidenceChat = enabled;
      saveSettings(settings);
    },
    /** Emulates SSE `banners` event with one new banner. Returns false when the queue is empty. */
    pushBanner() {
      const next = pushQueue.shift();
      if (!next) return false;
      banners = [next, ...banners];
      for (const listener of streamListeners) listener.onBanners([structuredClone(next)]);
      return pushQueue.length > 0;
    },
    reset() {
      banners = seedBanners();
      pushQueue = seedPushQueue();
      hiddenIds.clear();
    },
  };

  return { api, demo };
}

function loadSettings() {
  const defaults = { scenario: "normal", lowConfidenceChat: false };
  try {
    return { ...defaults, ...JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? "{}") };
  } catch {
    return defaults;
  }
}

function saveSettings(settings) {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch {
    // Private mode: settings just won't survive reload.
  }
}
