import { h } from "../ui/dom.js";
import { DEMO_COMPANIES, DEMO_PASSWORD } from "../auth/demo-accounts.js";
import { formatRubles } from "../auth/directory.js";
import { ICON_PATHS, svgIcon } from "./icons.js";

const ALTERNATIVE_METHODS = [
  { title: "Быстрый вход", icon: "dots" },
  { title: "Логин", icon: "lock" },
  { title: "QR-код", icon: "qr" },
  { title: "Токен", icon: "token" },
];

const INVALID_CREDENTIALS_MESSAGE = "Неверный телефон или пароль";

export function createLoginPage({ signInWithCredentials, signIn }) {
  return h(
    "div",
    { class: "login" },
    h(
      "section",
      { class: "login__screen" },
      createHeader(),
      createInfoStrip(),
      createForm({ signInWithCredentials }),
      createAlternativeMethods(),
    ),
    createDemoAccess({ signIn }),
  );
}

function createLogo() {
  return h("a", { class: "login-logo", href: "login.html", "aria-label": "Альфа-Бизнес" }, svgIcon(ICON_PATHS.logo, 30));
}

function createHeader() {
  return h("header", { class: "login__header" }, createLogo(), h("a", { class: "login__help", href: "#demo" }, "Помощь"));
}

function createInfoStrip() {
  return h(
    "div",
    { class: "login-strip", role: "note" },
    h("span", { class: "login-strip__icon" }, svgIcon(ICON_PATHS.shield, 22)),
    h(
      "div",
      {},
      h("p", { class: "login-strip__title" }, "Демо-стенд: данные компаний вымышлены"),
      h("p", { class: "login-strip__text" }, "Войдите по телефону и паролю или выберите сотрудника ниже"),
    ),
    h("a", { class: "login-strip__button", href: "#demo" }, "Подробнее"),
  );
}

function createForm({ signInWithCredentials }) {
  const phoneInput = h("input", {
    class: "login-input__control",
    id: "login-phone",
    type: "tel",
    inputmode: "tel",
    autocomplete: "username",
    placeholder: "Телефон",
    "aria-label": "Телефон",
  });
  const passwordInput = h("input", {
    class: "login-input__control",
    id: "login-password",
    type: "password",
    autocomplete: "current-password",
    placeholder: "Пароль",
    "aria-label": "Пароль",
  });
  const eyeButton = h(
    "button",
    { class: "login-input__eye", type: "button", "aria-label": "Показать пароль", "aria-pressed": "false" },
    svgIcon(ICON_PATHS.eyeOff, 24),
  );
  const error = h("p", { class: "login-form__error", role: "alert", hidden: true }, INVALID_CREDENTIALS_MESSAGE);

  eyeButton.addEventListener("click", () => {
    const shown = passwordInput.type === "text";
    passwordInput.type = shown ? "password" : "text";
    eyeButton.setAttribute("aria-pressed", String(!shown));
    eyeButton.setAttribute("aria-label", shown ? "Показать пароль" : "Скрыть пароль");
    eyeButton.replaceChildren(svgIcon(shown ? ICON_PATHS.eyeOff : ICON_PATHS.eye, 24));
  });

  const form = h(
    "form",
    { class: "login-form", novalidate: true },
    h("h1", { class: "login-form__title" }, "Альфа-Бизнес"),
    h("p", { class: "login-form__subtitle" }, "Вход по Alfa ID"),
    h("div", { class: "login-input" }, phoneInput),
    h("div", { class: "login-input" }, passwordInput, eyeButton),
    error,
    h("button", { class: "login-form__submit", type: "submit" }, "Войти"),
    h("button", { class: "login-form__forgot", type: "button" }, "Забыли пароль?"),
    h("p", { class: "login-form__hint" }, `Демо: телефон любого сотрудника из списка ниже, пароль ${DEMO_PASSWORD}`),
  );

  const resetError = () => {
    error.hidden = true;
  };
  phoneInput.addEventListener("input", resetError);
  passwordInput.addEventListener("input", resetError);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!signInWithCredentials(phoneInput.value, passwordInput.value)) error.hidden = false;
  });
  return form;
}

function createAlternativeMethods() {
  return h(
    "ul",
    { class: "login-methods" },
    ALTERNATIVE_METHODS.map(({ title, icon }) =>
      h(
        "li",
        {},
        h("button", { class: "login-method", type: "button" }, h("span", { class: "login-method__circle" }, svgIcon(ICON_PATHS[icon], 24)), title),
      ),
    ),
  );
}

function createDemoAccess({ signIn }) {
  return h(
    "section",
    { class: "demo-access", id: "demo", "aria-labelledby": "demo-access-title" },
    h("h2", { class: "demo-access__title", id: "demo-access-title" }, "Демо-доступ"),
    h(
      "p",
      { class: "demo-access__lead" },
      "Выберите компанию и сотрудника: кабинет и ассистент подстроятся под его роль. Все данные вымышлены.",
    ),
    h("div", { class: "demo-grid" }, DEMO_COMPANIES.map((company) => createCompanyCard(company, signIn))),
  );
}

function createCompanyCard(company, signIn) {
  return h(
    "article",
    { class: "demo-card" },
    h("h3", { class: "demo-card__name" }, company.short_name),
    h("p", { class: "demo-card__sector" }, company.okved_name),
    h("p", { class: "demo-card__description" }, company.description),
    h(
      "p",
      { class: "demo-card__facts" },
      h("span", {}, company.city),
      h("span", {}, `Выручка ${formatRevenue(company.revenue_rub)}`),
    ),
    h(
      "ul",
      { class: "demo-card__employees" },
      company.roles.map((employee) =>
        h(
          "li",
          {},
          h(
            "button",
            {
              class: "demo-employee",
              type: "button",
              onClick: () => signIn({ personPhone: employee.phone, companyId: company.company_id, role: employee.role }),
            },
            h("span", { class: "demo-employee__name" }, employee.full_name),
            h("span", { class: "demo-employee__title" }, employee.title),
          ),
        ),
      ),
    ),
  );
}

function formatRevenue(rubles) {
  const billions = rubles / 1e9;
  return billions >= 1 ? `${billions.toFixed(1).replace(".", ",")} млрд ₽` : formatRubles(rubles);
}
