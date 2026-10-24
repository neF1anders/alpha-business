import { collapseAndRemove, h } from "../../ui/dom.js";
import { inlineTextOf } from "../model.js";

/**
 * Figma `Assistant/InlineInsight`: purple strip on an action page.
 * Returns a slot right away; it expands once `GET /signals/inline` answers with an item.
 *
 * @param {{page: import("../model.js").InlinePage, store: any, service: any, spacing?: {top: string, bottom: string}}} options
 */
export function createInlineInsight({ page, store, service, spacing = { top: "0px", bottom: "0px" } }) {
  const content = h("div", {});
  const slot = h(
    "div",
    { class: "collapsible inline-slot is-collapsed", style: { "--slot-mt": spacing.top, "--slot-mb": spacing.bottom } },
    content,
  );

  service
    .getInlineSignal(page)
    .then(({ item }) => {
      if (!item || !slot.isConnected) return slot.remove();
      content.append(renderStrip(item, service, () => hide(item.banner_id)));
      requestAnimationFrame(() => slot.classList.remove("is-collapsed"));
      followRemoval(item.banner_id);
    })
    // The strip is optional: a failed request simply leaves the page without it.
    .catch(() => slot.remove());

  function hide(bannerId) {
    service.dismiss(bannerId);
    collapseAndRemove(slot);
  }

  /** Banner hidden or suppressed in the drawer disappears from the page too. */
  function followRemoval(bannerId) {
    const isPresent = (state) => state.signals.items.some((item) => item.banner_id === bannerId);
    let wasPresent = isPresent(store.get());
    const unsubscribe = store.subscribe((state) => {
      if (!slot.isConnected) return unsubscribe();
      const present = isPresent(state);
      if (wasPresent && !present && state.signals.status === "ready") {
        unsubscribe();
        collapseAndRemove(slot);
      }
      wasPresent = present;
    });
  }

  return slot;
}

function renderStrip(banner, service, onHide) {
  return h(
    "div",
    { class: "inline-insight", role: "note", dataset: { severity: banner.severity } },
    h("span", { class: "inline-insight__badge" }, h("span", { class: "sev-dot" }), "Ассистент"),
    h("p", { class: "inline-insight__text" }, inlineTextOf(banner)),
    h("button", { class: "inline-insight__discuss", type: "button", onClick: () => service.discuss(banner) }, "Обсудить"),
    h("button", { class: "inline-insight__hide", type: "button", onClick: onHide }, "Скрыть"),
  );
}
