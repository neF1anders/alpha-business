import { h, icon } from "./dom.js";

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

  const topbar = h(
    "header",
    { class: "topbar" },
    icon("mail", 18),
    icon("bell", 18),
    h(
      "div",
      { class: "company" },
      h("span", { class: "company__avatar" }, "С"),
      h("div", {}, h("p", { class: "company__name" }, "ООО «Северный ветер»"), h("p", { class: "company__note" }, "Демо-клиент")),
    ),
    h("span", { class: "user-pill" }, "Пользователь"),
    icon("sliders-16", 16),
  );

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
