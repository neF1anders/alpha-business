import { h, icon } from "../../ui/dom.js";
import { watch } from "../../state/store.js";
import { SEVERITIES, SEVERITY_INDICATOR_TITLES } from "../model.js";

/**
 * Vertical pill above the support button (Figma `Assistant/Panel`):
 * assistant button + three severity indicators with counters.
 */
export function createAssistantRail({ store, service }) {
  const assistantButton = h(
    "button",
    {
      class: "rail__assistant",
      type: "button",
      "aria-label": "Открыть ассистента",
      onClick: () => service.openDrawer({ tab: "banners", filter: "all" }),
    },
    icon("sparkle", 18),
  );

  const indicators = Object.fromEntries(SEVERITIES.map((severity) => [severity, createIndicator(severity, service)]));

  const rail = h(
    "div",
    { class: "rail" },
    assistantButton,
    h("div", { class: "rail__divider" }),
    SEVERITIES.map((severity) => indicators[severity].element),
  );

  const support = h(
    "button",
    { class: "support-button", type: "button", "aria-label": "Поддержка" },
    icon("support", 20),
  );

  watch(
    store,
    (state) => state.signals,
    (signals) => {
      for (const severity of SEVERITIES) {
        indicators[severity].setCount(signals.status === "ready" ? signals.counts[severity] : null);
      }
    },
  );

  watch(
    store,
    (state) => state.urgentPushAt,
    (pushedAt, previous) => {
      if (previous === undefined || !pushedAt) return;
      restartAnimation(assistantButton, "is-pulsing");
    },
  );

  return h("div", { class: "rail-dock" }, rail, support);
}

function createIndicator(severity, service) {
  const count = h("span", { class: "indicator__count" }, "–");
  const element = h(
    "button",
    {
      class: "indicator",
      type: "button",
      dataset: { severity },
      onClick: () => service.openDrawer({ tab: "banners", filter: severity }),
    },
    h("span", { class: "indicator__disc" }, icon(`sev-${severity}`, 14)),
    count,
  );

  return {
    element,
    setCount(value) {
      const text = value === null ? "–" : String(value);
      if (count.textContent !== text && value !== null) restartAnimation(count, "is-bumped");
      count.textContent = text;
      element.classList.toggle("is-zero", !value);
      element.setAttribute("aria-label", `${SEVERITY_INDICATOR_TITLES[severity]}: ${value ?? "загрузка"}`);
    },
  };
}

function restartAnimation(element, className) {
  element.classList.remove(className);
  // Force reflow so the same animation can play again.
  void element.offsetWidth;
  element.classList.add(className);
  element.addEventListener("animationend", () => element.classList.remove(className), { once: true });
}
