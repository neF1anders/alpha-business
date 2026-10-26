import { collapseAndRemove, collapsible, h, icon, replaceChildren } from "../../ui/dom.js";
import { watch } from "../../state/store.js";
import { SEVERITIES, SEVERITY_TITLES } from "../model.js";
import { createBannerCard } from "./banner-card.js";

const SKELETON_COUNT = 3;
const STAGGER_MS = 45;

/** "Баннеры" tab: severity filters + list with loading / empty / error states. */
export function createBannerPanel({ store, service, scrollRoot }) {
  const filters = createFilters(store, service);
  const list = h("div", { class: "banner-list" });
  const stateView = h("div", { class: "panel-state-slot" });
  const filteredEmpty = h("p", { class: "filtered-empty", hidden: true }, "В этой категории пока ничего нет");
  const body = h("div", { class: "drawer__scroll" }, stateView, list, filteredEmpty);

  /** banner_id -> collapsible wrapper */
  const rendered = new Map();
  const seenObserver = createSeenObserver(service, store, body);

  function renderState(status, itemsCount) {
    const view = status === "ready" && itemsCount === 0 ? "empty" : status === "ready" ? "list" : status;
    if (stateView.dataset.view === view) return;
    stateView.dataset.view = view;
    if (view === "loading" || view === "idle") replaceChildren(stateView, renderSkeletons());
    else if (view === "error") replaceChildren(stateView, renderError(() => service.loadSignals()));
    else if (view === "empty") replaceChildren(stateView, renderEmpty());
    else replaceChildren(stateView);
  }

  function syncItems(items, { animateEnter }) {
    const nextIds = new Set(items.map((item) => item.banner_id));
    for (const [id, wrapper] of rendered) {
      if (nextIds.has(id)) continue;
      rendered.delete(id);
      seenObserver.unobserve(wrapper);
      collapseAndRemove(wrapper);
    }

    let previous = null;
    items.forEach((item, index) => {
      let wrapper = rendered.get(item.banner_id);
      if (!wrapper) {
        wrapper = collapsible(createBannerCard(item, service), "banner-list__item");
        wrapper.dataset.id = item.banner_id;
        wrapper.dataset.severity = item.severity;
        if (animateEnter) {
          wrapper.classList.add("enter");
          wrapper.style.animationDelay = `${Math.min(index, 6) * STAGGER_MS}ms`;
          wrapper.addEventListener("animationend", () => wrapper.classList.remove("enter"), { once: true });
        } else {
          // Pushed banner: grow from zero height so the list below slides down smoothly.
          wrapper.classList.add("is-collapsed", "is-new");
          requestAnimationFrame(() => requestAnimationFrame(() => wrapper.classList.remove("is-collapsed")));
        }
        rendered.set(item.banner_id, wrapper);
        seenObserver.observe(wrapper);
      }
      // Moving a node restarts its CSS animations, so touch the DOM only when the order is really wrong.
      // Wrappers that are still collapsing out are skipped when checking the order.
      const nextLive = nextRenderedSibling(previous ? previous.nextSibling : list.firstChild);
      if (nextLive !== wrapper) list.insertBefore(wrapper, previous ? previous.nextSibling : list.firstChild);
      previous = wrapper;
    });
  }

  function nextRenderedSibling(node) {
    const live = new Set(rendered.values());
    while (node && !live.has(node)) node = node.nextSibling;
    return node;
  }

  /** Instant clear: the skeleton / error view replaces the list, nothing to animate. */
  function clearItems() {
    for (const wrapper of rendered.values()) seenObserver.unobserve(wrapper);
    rendered.clear();
    list.replaceChildren();
    filteredEmpty.hidden = true;
  }

  function applyFilter(filter) {
    let visible = 0;
    for (const wrapper of rendered.values()) {
      const matches = filter === "all" || wrapper.dataset.severity === filter;
      wrapper.classList.toggle("is-filtered-out", !matches);
      if (matches) visible += 1;
    }
    const { status } = store.get().signals;
    filteredEmpty.hidden = !(status === "ready" && rendered.size > 0 && visible === 0);
  }

  watch(
    store,
    (state) => state.signals,
    (signals, previous) => {
      renderState(signals.status, signals.items.length);
      // Counters are meaningless while loading or after an error.
      filters.classList.toggle("is-hidden", signals.status !== "ready");
      if (signals.status !== "ready") {
        clearItems();
        return;
      }
      const freshLoad = !previous || previous.status !== "ready";
      syncItems(signals.items, { animateEnter: freshLoad });
      applyFilter(store.get().drawer.filter);
    },
  );

  watch(store, (state) => state.drawer.filter, applyFilter);

  scrollRoot.append(filters, body);
}

function createFilters(store, service) {
  const options = [{ key: "all", title: "Все" }, ...SEVERITIES.map((key) => ({ key, title: SEVERITY_TITLES[key] }))];
  const buttons = options.map(({ key, title }) => {
    const count = h("span", { class: "filter-chip__count" });
    const button = h(
      "button",
      {
        class: "filter-chip",
        type: "button",
        dataset: { severity: key },
        "aria-pressed": "false",
        onClick: () => service.setFilter(key),
      },
      key !== "all" && h("span", { class: "sev-dot" }),
      title,
      count,
    );
    return { key, button, count };
  });

  watch(
    store,
    (state) => state.drawer.filter,
    (filter) => {
      for (const { key, button } of buttons) {
        button.classList.toggle("is-active", key === filter);
        button.setAttribute("aria-pressed", String(key === filter));
      }
    },
  );

  watch(
    store,
    (state) => state.signals.counts,
    (counts) => {
      for (const { key, count } of buttons) {
        if (key !== "all") count.textContent = ` ${counts[key]}`;
      }
    },
  );

  return h("div", { class: "filters", role: "toolbar", "aria-label": "Фильтр сигналов" }, buttons.map(({ button }) => button));
}

function renderSkeletons() {
  return h(
    "div",
    { class: "skeletons", "aria-busy": "true", "aria-label": "Загрузка сигналов" },
    Array.from({ length: SKELETON_COUNT }, (_, index) => h("div", { class: "skeleton", style: { "--i": index } })),
  );
}

function renderEmpty() {
  return h(
    "div",
    { class: "panel-state enter" },
    h("p", { class: "panel-state__title" }, "Пока нет новых сигналов"),
    h("p", { class: "panel-state__text" }, "Мы сообщим, когда появится важное для вашего бизнеса"),
  );
}

export function renderError(onRetry) {
  return h(
    "div",
    { class: "panel-state enter", role: "alert" },
    h("p", { class: "panel-state__title" }, "Не получилось загрузить"),
    h("p", { class: "panel-state__text" }, "Остальная страница работает как обычно"),
    h("button", { class: "chip-button chip-button--icon", type: "button", onClick: onRetry }, icon("refresh", 14), "Повторить"),
  );
}

/** Sends `seen` once a card has been visible in the open drawer. */
function createSeenObserver(service, store, root) {
  if (typeof IntersectionObserver === "undefined") return { observe() {}, unobserve() {} };
  const observer = new IntersectionObserver(
    (entries) => {
      const { drawer } = store.get();
      if (!drawer.open || drawer.tab !== "banners") return;
      for (const entry of entries) {
        if (entry.isIntersecting && !entry.target.classList.contains("is-filtered-out")) {
          service.markSeen(entry.target.dataset.id);
        }
      }
    },
    { root, threshold: 0.6 },
  );
  return observer;
}
