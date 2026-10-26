import { h, icon } from "./dom.js";
import { currentContext, signOut, switchCompany, switchEmployee } from "../auth/context.js";
import { findCompany } from "../auth/directory.js";

/**
 * Left menu: only the routes below lead to real pages, the rest open the stub (Figma 04).
 * `route` doubles as the hash: #/payments etc.
 */
export const NAV_ITEMS = [
  { route: "home", title: "Все сервисы", icon: "nav-services" },
  { divider: true },
  { route: "applications", title: "Заявки", icon: "nav-doc", badge: "НОВОЕ" },
  { route: "new-payment", title: "Новый платёж", icon: "nav-plus" },
  { route: "feed", title: "Лента операций", icon: "nav-feed" },
  { route: "in-work", title: "Платежи в работе", icon: "nav-clock" },
  { route: "registry-import", title: "Импорт реестров", icon: "nav-doc" },
  { route: "statement", title: "Выписка", icon: "nav-doc" },
  { route: "accounts", title: "Счета", icon: "nav-doc" },
  { route: "counterparties", title: "Контрагенты", icon: "nav-users" },
  { route: "certificates", title: "Справки", icon: "nav-doc" },
  { route: "invoices", title: "Счета на оплату", icon: "nav-doc" },
  { route: "cards", title: "Карты", icon: "nav-card" },
  { divider: true },
  { route: "procurement", title: "Госзакупки", icon: "nav-gov" },
];

export function createShell() {
  const navLinks = new Map();
  const nav = h(
    "nav",
    { class: "nav", "aria-label": "Основное меню" },
    NAV_ITEMS.map((item) => {
      if (item.divider) return h("div", { class: "nav__divider", role: "separator" });
      const link = h(
        "a",
        { class: "nav__item", href: `#/${item.route}` },
        icon(item.icon, 18),
        item.title,
        item.badge && h("span", { class: "nav__badge" }, item.badge),
      );
      navLinks.set(item.route, link);
      return link;
    }),
  );

  const sidebar = h(
    "aside",
    { class: "sidebar" },
    h("a", { class: "logo", href: "#/home" }, h("span", { class: "logo__mark" }, "A"), h("span", { class: "logo__name" }, "Альфа-Бизнес")),
    nav,
    h("a", { class: "nav__settings", href: "#/menu-settings" }, icon("sliders-16", 16), "Настроить меню"),
  );

  const topbar = h("header", { class: "topbar" }, icon("mail", 18), icon("bell", 18), createCompanyBlock(), createUserMenu(), icon("sliders-16", 16));

  const page = h("main", { class: "page", id: "page" });
  const element = h("div", { class: "shell" }, sidebar, h("div", { class: "main" }, topbar, page));

  return {
    element,
    page,
    setActive(route) {
      for (const [key, link] of navLinks) {
        const active = key === route;
        link.classList.toggle("is-active", active);
        if (active) link.setAttribute("aria-current", "page");
        else link.removeAttribute("aria-current");
      }
    },
  };
}

export function titleOf(route) {
  return NAV_ITEMS.find((item) => item.route === route)?.title ?? "Раздел";
}

const FALLBACK_COMPANY = { legal_name: "ООО «Северный ветер»", note: "Демо-клиент" };

function createCompanyBlock() {
  const { company } = currentContext();
  const name = company?.legal_name ?? FALLBACK_COMPANY.legal_name;
  const note = company ? `ИНН ${company.inn}` : FALLBACK_COMPANY.note;
  const avatarLetter = name.replace(/[^\p{L}]/gu, "").replace(/^ООО|^АО|^ПАО/u, "").charAt(0).toUpperCase() || "К";
  return h(
    "div",
    { class: "company" },
    h("span", { class: "company__avatar" }, avatarLetter),
    h("div", {}, h("p", { class: "company__name" }, name), h("p", { class: "company__note" }, note)),
  );
}

/** User block with a dropdown: company switcher, demo employee switcher, sign out. */
function createUserMenu() {
  const { session, company, employee, memberships } = currentContext();
  if (!session) return h("span", { class: "user-pill" }, employee?.full_name ?? "Пользователь");

  const menu = h("div", { class: "user-menu__panel", role: "menu", hidden: true });
  const trigger = h(
    "button",
    { class: "user-menu__trigger", type: "button", "aria-haspopup": "menu", "aria-expanded": "false" },
    h("span", { class: "user-menu__name" }, employee?.full_name ?? "Пользователь"),
    h("span", { class: "user-menu__title" }, employee?.title ?? ""),
  );
  const root = h("div", { class: "user-menu" }, trigger, menu);

  const close = () => {
    menu.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
  };
  trigger.addEventListener("click", () => {
    menu.hidden = !menu.hidden;
    trigger.setAttribute("aria-expanded", String(!menu.hidden));
  });
  document.addEventListener("click", (event) => {
    if (!root.contains(event.target)) close();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") close();
  });

  const item = (label, hint, active, onSelect) =>
    h(
      "button",
      { class: ["user-menu__item", active && "is-active"], type: "button", role: "menuitem", onClick: onSelect },
      h("span", {}, label),
      hint && h("span", { class: "user-menu__hint" }, hint),
    );

  if (memberships.length > 1) {
    menu.append(
      h("p", { class: "user-menu__label" }, "Сменить компанию"),
      ...memberships.map(({ company_id }) => {
        const target = findCompany(company_id);
        return item(target.short_name, target.legal_name, company_id === company?.company_id, () => switchCompany(company_id));
      }),
    );
  }
  if (company) {
    menu.append(
      h("p", { class: "user-menu__label" }, "Сменить сотрудника (демо)"),
      ...company.roles.map((candidate) =>
        item(candidate.full_name, candidate.title, candidate.role === employee?.role, () => switchEmployee(candidate.role)),
      ),
    );
  }
  menu.append(h("button", { class: "user-menu__item user-menu__item--exit", type: "button", role: "menuitem", onClick: signOut }, "Выйти"));
  return root;
}
