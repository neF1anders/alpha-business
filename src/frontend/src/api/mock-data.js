/**
 * Fixtures for the mock backend. All companies, numbers and texts are fictional
 * (same rule as the Figma file: nothing from interface/leaks/ is reused).
 */

const HOUR = 3_600_000;
const inHours = (hours) => new Date(Date.now() + hours * HOUR).toISOString();

const SOURCES = {
  cbr: { kind: "primary", title: "Банк России", url: "https://www.cbr.ru/", ref_id: "n-cbr" },
  bankAnalytics: { kind: "bank", title: "Аналитика банка", url: "https://alfabank.ru/", ref_id: "d-bank-4" },
  rbc: { kind: "media", title: "РБК", url: "https://www.rbc.ru/", ref_id: "n-rbc" },
  rosstat: { kind: "primary", title: "Росстат", url: "https://rosstat.gov.ru/", ref_id: "n-rosstat" },
  minfin: { kind: "primary", title: "Минфин", url: "https://minfin.gov.ru/", ref_id: "n-minfin" },
  kommersant: { kind: "media", title: "Коммерсантъ", url: "https://www.kommersant.ru/", ref_id: "n-kommersant" },
  vedomosti: { kind: "media", title: "Ведомости", url: "https://www.vedomosti.ru/", ref_id: "n-vedomosti" },
  minprom: { kind: "primary", title: "Минпромторг", url: "https://minpromtorg.gov.ru/", ref_id: "n-minprom" },
  zakupki: { kind: "primary", title: "ЕИС Закупки", url: "https://zakupki.gov.ru/", ref_id: "n-zakupki" },
  msp: { kind: "primary", title: "Корпорация МСП", url: "https://corpmsp.ru/", ref_id: "n-msp" },
};

/** @returns {import("../assistant/model.js").BannerDTO[]} */
export function createInitialBanners() {
  return [
    {
      banner_id: "b-rate",
      severity: "attention",
      label: "Внимание · ставка",
      headline: "Рост ставок по кредитам",
      for_you: "У вас 3 кредитных продукта: платёж по ним вырастет.",
      caveat: false,
      raw_news: false,
      sources: [SOURCES.cbr, SOURCES.bankAnalytics],
      expires_at: inHours(36),
      inline_text:
        "Лизинговая компания (демо): ставка по вашим кредитным продуктам может вырасти, платёж по лизингу — 2 060 000 ₽. Это оценка модели, проверьте источник.",
    },
    {
      banner_id: "b-fx-control",
      severity: "attention",
      label: "Новость",
      headline: "Банк России опубликовал проект изменений по валютному контролю",
      for_you: "",
      caveat: false,
      raw_news: true,
      sources: [SOURCES.cbr],
      expires_at: inHours(20),
    },
    {
      banner_id: "b-cny",
      severity: "watch",
      label: "Наблюдение · курс",
      headline: "Курс юаня торгуется в узком коридоре",
      for_you: "У вас есть расчёты в CNY, держите запас на счёте.",
      caveat: false,
      raw_news: false,
      sources: [SOURCES.cbr, SOURCES.rbc],
      expires_at: inHours(36),
      inline_text:
        "Для платежей в юанях: курс CNY держится в узком коридоре. Это наблюдение по данным ЦБ, не рекомендация.",
    },
    {
      banner_id: "b-tax",
      severity: "watch",
      label: "Наблюдение · налоги",
      headline: "Возможно снижение налоговой нагрузки",
      for_you: "Может коснуться вашей системы налогообложения.",
      caveat: true,
      raw_news: false,
      sources: [SOURCES.minfin, SOURCES.kommersant],
      expires_at: inHours(30),
    },
    {
      banner_id: "b-logistics",
      severity: "watch",
      label: "Наблюдение · логистика",
      headline: "Тарифы на перевозки на юг растут",
      for_you: "Часть ваших контрагентов в Краснодарском крае: проверьте сроки поставок.",
      caveat: false,
      raw_news: false,
      sources: [SOURCES.vedomosti],
      expires_at: inHours(12),
    },
    {
      banner_id: "b-demand",
      severity: "opportunity",
      label: "Возможность · спрос",
      headline: "Спрос в вашей отрасли восстанавливается",
      for_you: "Подойдёт ли вам овердрафт под сезон?",
      caveat: false,
      raw_news: false,
      sources: [SOURCES.bankAnalytics, SOURCES.rosstat],
      expires_at: inHours(36),
      inline_text:
        "«Ритейл-Юг» (демо): сигнал по спросу. Это 14% ваших входящих платежей. Связь по платежам показывает взаимодействие, а не его характер, вывод об отрасли — гипотеза.",
    },
    {
      banner_id: "b-subsidy",
      severity: "opportunity",
      label: "Возможность · субсидии",
      headline: "Субсидии на модернизацию оборудования",
      for_you: "Компании вашей отрасли могут подать заявку до конца квартала.",
      caveat: false,
      raw_news: false,
      sources: [SOURCES.minprom],
      expires_at: inHours(48),
    },
    {
      banner_id: "b-tenders",
      severity: "opportunity",
      label: "Возможность · госзакупки",
      headline: "Закупок по вашему ОКВЭД стало больше",
      for_you: "За месяц в вашей категории на 18% больше процедур.",
      caveat: true,
      raw_news: false,
      sources: [SOURCES.zakupki],
      expires_at: inHours(40),
    },
    {
      banner_id: "b-leasing",
      severity: "opportunity",
      label: "Возможность · лизинг",
      headline: "Льготный лизинг для МСП продлили",
      for_you: "У вас уже есть лизинг: можно сравнить условия.",
      caveat: false,
      raw_news: false,
      sources: [SOURCES.msp, SOURCES.bankAnalytics],
      expires_at: inHours(60),
    },
  ];
}

/** Banners the "push" demo button sends, in order. */
export function createPushQueue() {
  return [
    {
      banner_id: "b-push-sanctions",
      severity: "attention",
      label: "Внимание · расчёты",
      headline: "Банки-корреспонденты ужесточают проверки платежей в CNY",
      for_you: "У вас регулярные исходящие платежи в юанях: закладывайте больше времени.",
      caveat: false,
      raw_news: false,
      sources: [SOURCES.cbr, SOURCES.rbc],
      expires_at: inHours(24),
    },
    {
      banner_id: "b-push-payroll",
      severity: "opportunity",
      label: "Возможность · зарплатный проект",
      headline: "Снижены тарифы на выплаты самозанятым",
      for_you: "Вы платите самозанятым каждый месяц — тариф станет ниже.",
      caveat: false,
      raw_news: false,
      sources: [SOURCES.bankAnalytics],
      expires_at: inHours(72),
    },
    {
      banner_id: "b-push-energy",
      severity: "watch",
      label: "Наблюдение · тарифы",
      headline: "Регионы обсуждают рост тарифов на электроэнергию для бизнеса",
      for_you: "Для производственной компании это может заметно изменить себестоимость.",
      caveat: true,
      raw_news: false,
      sources: [SOURCES.kommersant],
      expires_at: inHours(36),
    },
  ];
}

/** Which banner is shown inline on which action page (backend: `placements`). */
export const INLINE_PLACEMENTS = {
  payments: { bannerId: "b-demand", context: { counterparty: "ООО «Ритейл-Юг» (демо)" } },
  counterparties: { bannerId: "b-rate", context: { counterparty: "Лизинговая компания (демо)" } },
  new_payment: { bannerId: "b-cny", context: {} },
};

const RATE_ANSWER = {
  claims: [
    {
      type: "fact",
      text: "Банк России изменил ключевую ставку на последнем заседании.",
      evidence: [{ ref_id: "n-cbr" }],
    },
    {
      type: "bank",
      text: "По оценке аналитиков банка, ставки по кредитам для малого бизнеса будут пересматриваться в течение месяца.",
      evidence: [{ ref_id: "d-bank-4" }],
    },
    { type: "model", text: "Для ваших 3 кредитных продуктов ежемесячный платёж может вырасти.", evidence: [] },
    {
      type: "hypothesis",
      text: "Если ставка удержится надолго, нагрузка на оборотный капитал вырастет.",
      evidence: [],
    },
  ],
  sources: [SOURCES.cbr, { ...SOURCES.bankAnalytics, title: "Аналитика банка, стр. 4" }],
  caveat: true,
  follow_ups: ["Что будет с платежом по кредиту?", "Как подготовиться к росту ставки?"],
  confidence: 0.64,
};

const CNY_ANSWER = {
  claims: [
    { type: "fact", text: "Курс юаня последние две недели держится в узком диапазоне.", evidence: [{ ref_id: "n-cbr" }] },
    {
      type: "bank",
      text: "Аналитики банка не ждут резких движений до конца месяца.",
      evidence: [{ ref_id: "d-bank-4" }],
    },
    { type: "model", text: "Для ваших платежей в CNY запас 5–7% на счёте покроет обычные колебания.", evidence: [] },
  ],
  sources: [SOURCES.cbr, SOURCES.bankAnalytics],
  caveat: false,
  follow_ups: ["Когда лучше покупать юани?", "Как зафиксировать курс?"],
  confidence: 0.78,
};

const DEMAND_ANSWER = {
  claims: [
    { type: "fact", text: "Оборот розничной торговли в регионе вырос третий месяц подряд.", evidence: [{ ref_id: "n-rosstat" }] },
    {
      type: "model",
      text: "«Ритейл-Юг» (демо) даёт 14% ваших входящих платежей — рост его спроса может увеличить ваши поступления.",
      evidence: [],
    },
    {
      type: "hypothesis",
      text: "Связь по платежам показывает взаимодействие, а не его характер: вывод об отрасли — гипотеза.",
      evidence: [],
    },
  ],
  sources: [SOURCES.rosstat, SOURCES.bankAnalytics],
  caveat: true,
  follow_ups: ["Нужен ли мне овердрафт под сезон?", "Как проверить надёжность контрагента?"],
  confidence: 0.61,
};

const GENERIC_ANSWER = {
  claims: [
    {
      type: "bank",
      text: "По данным банка, у компаний вашего сегмента сейчас главные темы — ставка и курс юаня.",
      evidence: [{ ref_id: "d-bank-4" }],
    },
    { type: "model", text: "Для вашей компании важнее всего следить за платежами по кредитам.", evidence: [] },
  ],
  sources: [SOURCES.bankAnalytics],
  caveat: true,
  follow_ups: ["Как ключевая ставка повлияет на мои кредиты?", "Что происходит с курсом юаня?"],
  confidence: 0.55,
};

const RAW_NEWS_ANSWER = {
  claims: [{ type: "fact", text: "Банк России опубликовал проект изменений по валютному контролю.", evidence: [{ ref_id: "n-cbr" }] }],
  sources: [SOURCES.cbr],
  caveat: true,
  raw_news: true,
  follow_ups: [],
  confidence: 0.2,
};

/** Picks a canned answer by banner context or keywords in the question. */
export function pickAnswer({ question, bannerId, forceRawNews }) {
  if (forceRawNews || bannerId === "b-fx-control") return RAW_NEWS_ANSWER;
  if (bannerId === "b-cny" || /юан|cny|курс/i.test(question)) return CNY_ANSWER;
  if (bannerId === "b-demand" || /спрос|ритейл|овердрафт/i.test(question)) return DEMAND_ANSWER;
  if (bannerId === "b-rate" || /ставк|кредит|платёж|платеж/i.test(question)) return RATE_ANSWER;
  return GENERIC_ANSWER;
}
