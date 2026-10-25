import { h } from "./dom.js";

const SCENARIOS = [
  { key: "normal", title: "Обычный" },
  { key: "slow", title: "Медленно (скелетоны)" },
  { key: "empty", title: "Нет сигналов" },
  { key: "error", title: "Ошибка бэка" },
];

/**
 * Floating "Демо" menu, mock mode only: switch backend scenario, send a push,
 * force a low-confidence chat answer. Not part of the product UI.
 */
export function createDemoPanel({ demo, service, router }) {
  const settings = demo.getSettings();

  const reload = () => {
    service.loadSignals();
    router.refresh();
  };

  const scenarioInputs = SCENARIOS.map(({ key, title }) =>
    h(
      "label",
      { class: "demo__option" },
      h("input", {
        type: "radio",
        name: "demo-scenario",
        value: key,
        checked: settings.scenario === key,
        onChange: () => {
          demo.setScenario(key);
          reload();
        },
      }),
      title,
    ),
  );

  const pushButton = h(
    "button",
    {
      class: "demo__button",
      type: "button",
      onClick: () => {
        const hasMore = demo.pushBanner();
        if (!hasMore) {
          pushButton.disabled = true;
          pushButton.textContent = "Push-очередь пуста";
        }
      },
    },
    "Прислать новый сигнал (push)",
  );

  const lowConfidence = h(
    "label",
    { class: "demo__option" },
    h("input", {
      type: "checkbox",
      checked: settings.lowConfidenceChat,
      onChange: (event) => demo.setLowConfidenceChat(event.target.checked),
    }),
    "Чат: низкая уверенность (raw_news)",
  );

  const resetButton = h(
    "button",
    {
      class: "demo__button demo__button--ghost",
      type: "button",
      onClick: () => {
        demo.reset();
        pushButton.disabled = false;
        pushButton.textContent = "Прислать новый сигнал (push)";
        reload();
      },
    },
    "Сбросить данные",
  );

  const popover = h(
    "div",
    { class: "demo__popover", id: "demo-popover" },
    h("p", { class: "demo__heading" }, "Сценарий бэка (mock)"),
    scenarioInputs,
    h("div", { class: "demo__divider" }),
    pushButton,
    lowConfidence,
    resetButton,
  );

  const toggle = h(
    "button",
    {
      class: "demo__toggle",
      type: "button",
      "aria-expanded": "false",
      "aria-controls": "demo-popover",
      onClick: () => {
        const open = root.classList.toggle("is-open");
        toggle.setAttribute("aria-expanded", String(open));
      },
    },
    "Демо",
  );

  const root = h("div", { class: "demo" }, popover, toggle);
  return root;
}
