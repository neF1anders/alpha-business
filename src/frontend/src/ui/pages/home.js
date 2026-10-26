import { h, icon } from "../dom.js";
import { config } from "../../config.js";
import { findCompany, formatRubles } from "../../auth/directory.js";

/** Figma 01: static summary of the fictional client. Only the assistant is interactive. */
export function renderHomePage() {
  return [renderQuickActions(), renderBalanceBlock(), renderSummaryTabs(), renderSummaryCards()];
}

export function renderQuickActions() {
  return h(
    "div",
    { class: "quick-actions" },
    h("button", { class: "quick-action", type: "button" }, icon("download", 16), "Скачать выписку"),
    h("button", { class: "quick-action", type: "button" }, icon("import", 16), "Импорт платежей"),
    h("a", { class: "quick-action", href: "#/new-payment" }, icon("plus-16", 16), "Создать платёж"),
    h("button", { class: "quick-action quick-action--muted", type: "button" }, "Ещё"),
  );
}

const TASKS = [
  { title: "Подписать и отправить", count: 1, hot: true },
  { title: "В работе у банка", count: 7, hot: true },
  { title: "Отклонённые", count: 4, hot: true },
  { title: "Черновики", count: 1 },
  { title: "Оплатить", count: 1 },
  { title: "Доработать", count: 0, dim: true },
  { title: "Принять предложение", count: 3 },
];

/** Account figures of the signed-in demo company; null for ids outside the demo directory. */
function summarizeAccounts(company) {
  if (!company) return null;
  const isForeign = (account) => /\b(?!RUB\b)[A-Z]{3}$/.test(account.name);
  const ruble = company.accounts.filter((account) => !isForeign(account));
  const foreign = company.accounts.filter(isForeign);
  const rubleTotal = ruble.reduce((sum, account) => sum + account.balance_rub, 0);
  return {
    amount: `${new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2 }).format(rubleTotal)} RUB`,
    foreign: foreign.map((account) => `${account.name.split(" ").pop()} · ${formatRubles(account.balance_rub)} экв.`),
    hint: `${ruble.length} в рублях, ${foreign.length} в валюте`,
  };
}

const LEGACY_BALANCE = {
  amount: "1 248 300,00 RUB",
  overdraft: "↳ 1 810 300,00 RUB с овердрафтом",
  foreign: ["4 000,00 CNY", "40 000,00 KZT"],
  extra: "+2",
  hint: "8 в рублях, 2 в валюте",
};

function renderBalanceBlock() {
  const summary = summarizeAccounts(findCompany(config.companyId));
  const balance = h(
    "div",
    { class: "glass-card balance" },
    h(
      "div",
      {},
      h("p", { class: "balance__amount" }, summary?.amount ?? LEGACY_BALANCE.amount),
      !summary && h("p", { class: "balance__sub" }, LEGACY_BALANCE.overdraft),
      h(
        "div",
        { class: "balance__currencies" },
        (summary?.foreign ?? LEGACY_BALANCE.foreign).map((text) => h("span", {}, text)),
        !summary && h("span", { class: "glass-pill" }, LEGACY_BALANCE.extra),
      ),
    ),
    h(
      "div",
      { class: "balance__promo" },
      h("span", { class: "balance__promo-icon" }, icon("check-12", 12)),
      "Переводите себе бесплатно 5 000 000 ₽",
      h("span", { class: "balance__promo-tag" }, "ОДОБРЕНО"),
    ),
    h(
      "div",
      { class: "balance__shortcuts" },
      h(
        "div",
        { class: "shortcut" },
        h("span", { class: "shortcut__icon" }, icon("accounts", 18)),
        h("div", {}, h("p", {}, "Счета"), h("p", { class: "shortcut__hint" }, summary?.hint ?? LEGACY_BALANCE.hint)),
      ),
      h("div", { class: "shortcut" }, h("span", { class: "shortcut__icon" }, icon("plus-dark", 18)), "Заказать бизнес-карту"),
    ),
  );

  const tasks = h(
    "div",
    { class: "glass-card tasks" },
    h("p", { class: "tasks__title" }, "Дела в работе"),
    h(
      "ul",
      { class: "tasks__list" },
      TASKS.map((task) =>
        h(
          "li",
          { class: ["tasks__row", task.dim && "is-dim"] },
          task.title,
          h("span", { class: ["tasks__count", task.hot && "is-hot"] }, task.count),
        ),
      ),
    ),
  );

  const smartCard = h(
    "div",
    { class: "glass-card smart-card" },
    h("p", { class: "smart-card__title" }, "Умная карта для всех"),
    h("p", { class: "smart-card__text" }, "Кэшбэк, бонусы и удобные платежи"),
    h("div", { class: "smart-card__art", "aria-hidden": "true" }),
  );

  return h(
    "section",
    { class: "balance-block", "aria-label": "Баланс" },
    balance,
    tasks,
    smartCard,
    h("div", { class: "balance-block__links" }, h("span", {}, "Реквизиты"), h("span", {}, "Тариф")),
  );
}

function renderSummaryTabs() {
  return h(
    "div",
    { class: "summary-tabs" },
    h(
      "div",
      { class: "summary-tabs__list" },
      h("span", { class: "is-active" }, "Сводка"),
      h("span", {}, "Счета на главной"),
      h("span", {}, "Мультибанк"),
      h("span", {}, "Металлы"),
    ),
    h("div", { class: "summary-tabs__tools" }, h("span", { class: "icon-chip" }, icon("eye", 16)), icon("sliders-18", 18)),
  );
}

function renderSummaryCards() {
  const application = (name, amount) =>
    h(
      "div",
      { class: "application" },
      h("p", { class: "application__name" }, name),
      h("div", { class: "application__amount" }, amount, h("span", { class: "status-tag status-tag--blue" }, "НА ПОДПИСЬ")),
      h("p", { class: "application__date" }, "24.10.2026"),
    );

  return h(
    "div",
    { class: "summary-cards" },
    h(
      "div",
      { class: "card" },
      h("p", { class: "card__title" }, "Активные заявки", h("span", { class: "count-chip" }, "9")),
      application("Лизинг", "75 000 ₽"),
      application("Факторинг", "120 000 ₽"),
    ),
    h(
      "div",
      { class: "card risk" },
      h("p", { class: "card__title" }, "Индикатор риска"),
      h("img", { src: "assets/icons/gauge.svg", width: 100, height: 100, alt: "" }),
      h("p", {}, "Низкий риск"),
    ),
    h("div", { class: "story" }, "Как получить деньги на бизнес"),
    h("div", { class: "story story--red" }, "Новое в Альфа-Бизнесе"),
  );
}
