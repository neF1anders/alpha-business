import { h, icon } from "../dom.js";
import { createInlineInsight } from "../../assistant/ui/inline-insight.js";
import { renderQuickActions } from "./home.js";
import { config } from "../../config.js";
import { findCompany } from "../../auth/directory.js";

/** Counterparty rows of the signed-in demo company: name, sector and trade direction. */
function demoCounterparties() {
  const groups = findCompany(config.companyId)?.counterparties ?? [];
  return groups.flatMap(({ names, sector, direction }) => names.slice(0, 1).map((name) => ({ name, sector, direction })));
}

/** Figma 06 / 06b: "Последние платежи" with the insight strip above the rows. */
export function renderPaymentsPage({ store, service }) {
  const amounts = ["12 400 000", "8 150 000", "2 060 000", "5 000 000"];
  const partners = demoCounterparties().slice(0, amounts.length);
  const rows = partners.length
    ? partners.map(({ name, direction }, index) =>
        direction === "buyer"
          ? [name, "Входящий", `+ ${amounts[index]} ₽`]
          : [name, "Исходящий", `– ${amounts[index]} ₽`],
      )
    : [
        ["ООО «Ритейл-Юг» (демо)", "Входящий", "+ 12 400 000 ₽"],
        ["АО «СтальПоставка» (демо)", "Исходящий", "– 8 150 000 ₽"],
        ["Лизинговая компания (демо)", "Исходящий", "– 2 060 000 ₽"],
        ["ООО «Север-Логистика» (своя группа)", "Внутренний", "– 5 000 000 ₽"],
      ];
  return [
    h("h1", { class: "page-title" }, "Платежи"),
    h(
      "section",
      { class: "surface payments-card" },
      h("h2", { class: "payments-card__title" }, "Последние платежи"),
      createInlineInsight({ page: "payments", store, service, spacing: { top: "0px", bottom: "0px" } }),
      rows.map(([name, kind, amount]) =>
        h(
          "div",
          { class: "table-row payments-row" },
          h("span", { class: "payments-row__name" }, name),
          h("span", { class: "muted" }, kind),
          h("span", { class: "payments-row__amount" }, amount),
        ),
      ),
    ),
  ];
}

/** Figma 07: the strip replaces the promo line between "Мои контрагенты" and search. */
export function renderCounterpartiesPage({ store, service }) {
  const partners = demoCounterparties().slice(0, 6);
  const rows = partners.length
    ? partners.map(({ name, sector }, index) => [name, sector, `•• ${String(42 + index * 76).padStart(4, "0")}`, "ИНН скрыт"])
    : [
        ["Лизинговая компания (демо)", "г. Москва", "•• 0042", "ИНН 770000••••"],
        ["ООО «Ритейл-Юг» (демо)", "г. Краснодар", "•• 0118", "ИНН 230000••••"],
        ["АО «СтальПоставка» (демо)", "г. Липецк", "•• 0207", "ИНН 480000••••"],
      ];
  return [
    h("h1", { class: "page-title" }, "Контрагенты"),
    h("section", { class: "cp-hero" }, h("span", { class: "cp-hero__icon" }, icon("nav-users", 18)), "Мои контрагенты"),
    createInlineInsight({ page: "counterparties", store, service, spacing: { top: "14px", bottom: "8px" } }),
    h("div", { class: "cp-search", style: { "margin-top": "14px" } }, icon("search", 16), "Название или ИНН"),
    h(
      "div",
      { class: "cp-filters" },
      ["Регион", "Тип контрагентов", "Российские", "Иностранные"].map((title) => h("button", { class: "cp-filter", type: "button" }, title)),
    ),
    h(
      "section",
      { class: "surface cp-table" },
      h(
        "div",
        { class: "table-row cp-row cp-row--head" },
        h("span", {}, "НАЗВАНИЕ И РЕГИОН"),
        h("span", {}, "СЧЁТ"),
        h("span", {}, "РЕКВИЗИТЫ"),
        h("span", {}, "СТАТУС"),
      ),
      rows.map(([name, region, account, inn]) =>
        h(
          "div",
          { class: "table-row cp-row" },
          h("div", {}, h("p", { class: "cp-row__name" }, name), h("p", { class: "cp-row__region" }, region)),
          h("span", {}, account),
          h("span", {}, inn),
          h("span", {}, h("span", { class: "status-tag status-tag--green" }, "НАДЁЖНЫЙ")),
        ),
      ),
    ),
  ];
}

const RUBLE_TILES = [
  ["Юрлицу"],
  ["Физлицу"],
  ["Самозанятому", "Физлицу или ИП в статусе самозанятого"],
  ["Государству", "В бюджетные организации"],
  ["Налоговой", "Пополнение единого налогового счёта"],
  ["Нерезиденту в рублях", "Иностранному юрлицу или физлицу"],
  ["По номеру карты", "Физлицам и юрлицам"],
  ["По номеру телефона", "Переводы физлицам через СБП"],
];
const CURRENCY_TILES = [["Валютный перевод"], ["Обмен валюты"]];

/** Figma 08: strip above "Рублёвые платежи" in the left column; the right column does not move. */
export function renderNewPaymentPage({ store, service }) {
  const tiles = (items) =>
    h(
      "div",
      { class: "np-grid" },
      items.map(([title, hint]) => h("button", { class: "tile", type: "button" }, title, hint && h("span", { class: "tile__hint" }, hint))),
    );

  return [
    h("h1", { class: "page-title" }, "Новый платёж"),
    h("div", { style: { "max-width": "660px" } }, createInlineInsight({ page: "new_payment", store, service, spacing: { top: "18px", bottom: "0px" } })),
    h(
      "div",
      { class: "np-layout" },
      h(
        "div",
        {},
        h("h2", { class: "np-section-title" }, "Рублёвые платежи"),
        tiles(RUBLE_TILES),
        h("h2", { class: "np-section-title" }, "Валютные операции"),
        tiles(CURRENCY_TILES),
      ),
      h(
        "aside",
        { class: "import-card" },
        h("p", { class: "import-card__title" }, "Импорт платежей и реестров"),
        h("p", { class: "import-card__text" }, "Выберите файл с рублёвыми платежами и реестрами"),
        h("button", { class: "outline-button", type: "button" }, "Импортировать"),
      ),
    ),
  ];
}

/** Figma 04: target for menu items outside the MVP. */
export function renderStubPage(title) {
  return [
    renderQuickActions(),
    h(
      "section",
      { class: "stub", "aria-label": title },
      h("h1", { class: "stub__title" }, "Раздел вне MVP"),
      h("p", { class: "stub__text" }, `«${title}»: здесь будет оригинальный экран Альфа-Бизнеса. В прототипе кликабельны только левое меню и ассистент.`),
    ),
  ];
}
