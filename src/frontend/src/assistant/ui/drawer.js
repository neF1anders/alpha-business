import { h, icon } from "../../ui/dom.js";
import { watch } from "../../state/store.js";
import { createBannerPanel } from "./banner-list.js";
import { createChatPanel } from "./chat.js";

const TABS = [
  { key: "banners", title: "Баннеры" },
  { key: "chat", title: "Чат" },
];

/** Right drawer (Figma `Assistant drawer`): header, tabs, banners / chat, question input. */
export function createAssistantDrawer({ store, service }) {
  const closeButton = h(
    "button",
    { class: "drawer__close", type: "button", "aria-label": "Закрыть ассистента", onClick: service.closeDrawer },
    icon("close-18", 18),
  );

  const header = h(
    "header",
    { class: "drawer__header" },
    h("span", { class: "assistant-avatar" }, icon("sparkle-16", 16)),
    h("h2", { class: "drawer__title", id: "assistant-title" }, "Ассистент"),
    closeButton,
  );

  const tabs = createTabs(store, service);

  const bannersPanel = h("section", { class: "drawer__panel", dataset: { tab: "banners" }, "aria-label": "Баннеры" });
  const chatPanel = h("section", { class: "drawer__panel", dataset: { tab: "chat" }, "aria-label": "Чат" });
  createBannerPanel({ store, service, scrollRoot: bannersPanel });
  createChatPanel({ store, service, scrollRoot: chatPanel });

  const drawer = h(
    "aside",
    { class: "drawer", role: "dialog", "aria-modal": "true", "aria-labelledby": "assistant-title", inert: true },
    header,
    tabs,
    h("div", { class: "drawer__panels" }, bannersPanel, chatPanel),
    createQuestionInput(store, service),
  );

  const scrim = h("div", { class: "scrim", onClick: service.closeDrawer });

  let returnFocusTo = null;
  watch(
    store,
    (state) => state.drawer.open,
    (open, wasOpen) => {
      document.documentElement.classList.toggle("is-drawer-open", open);
      drawer.inert = !open;
      if (open) {
        returnFocusTo = document.activeElement;
        // Focus after the slide-in starts, otherwise the browser scrolls the off-screen drawer into view.
        setTimeout(() => closeButton.focus({ preventScroll: true }), 50);
      } else if (wasOpen) {
        returnFocusTo?.focus?.({ preventScroll: true });
      }
    },
  );

  watch(
    store,
    (state) => state.drawer.tab,
    (tab) => {
      bannersPanel.classList.toggle("is-active", tab === "banners");
      chatPanel.classList.toggle("is-active", tab === "chat");
      bannersPanel.inert = tab !== "banners";
      chatPanel.inert = tab !== "chat";
    },
  );

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && store.get().drawer.open) service.closeDrawer();
  });

  return [scrim, drawer];
}

function createTabs(store, service) {
  const buttons = TABS.map(({ key, title }) =>
    h("button", { class: "tabs__tab", type: "button", role: "tab", dataset: { tab: key }, onClick: () => service.setTab(key) }, title),
  );
  const container = h("div", { class: "tabs", role: "tablist" }, h("span", { class: "tabs__thumb", "aria-hidden": "true" }), buttons);

  watch(
    store,
    (state) => state.drawer.tab,
    (tab) => {
      container.dataset.active = tab;
      for (const button of buttons) {
        const active = button.dataset.tab === tab;
        button.classList.toggle("is-active", active);
        button.setAttribute("aria-selected", String(active));
      }
    },
  );
  return container;
}

function createQuestionInput(store, service) {
  const input = h("input", {
    class: "ask__input",
    type: "text",
    placeholder: "Спросите ассистента…",
    "aria-label": "Вопрос ассистенту",
    autocomplete: "off",
    maxlength: 500,
  });
  const send = h("button", { class: "ask__send", type: "submit", "aria-label": "Отправить", disabled: true }, icon("send", 18));

  const updateSendState = () => {
    send.disabled = !input.value.trim() || store.get().chat.pending;
  };
  input.addEventListener("input", updateSendState);
  watch(store, (state) => state.chat.pending, updateSendState);

  return h(
    "form",
    {
      class: "ask",
      onSubmit: (event) => {
        event.preventDefault();
        const question = input.value.trim();
        if (!question || store.get().chat.pending) return;
        service.setTab("chat");
        service.ask(question);
        input.value = "";
        updateSendState();
      },
    },
    h(
      "label",
      { class: "ask__field" },
      input,
      h(
        "button",
        { class: "ask__mic", type: "button", disabled: true, title: "Голосовой ввод — после MVP", "aria-label": "Голосовой ввод недоступен" },
        icon("mic", 16),
      ),
    ),
    send,
  );
}
