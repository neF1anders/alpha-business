import { h, icon } from "../../ui/dom.js";
import { formatExpiresIn } from "./format.js";

/**
 * Figma `Banner/Card`: variants attention / watch / opportunity / caveat / raw_news.
 * @param {import("../model.js").BannerDTO} banner
 */
export function createBannerCard(banner, service) {
  const card = h(
    "article",
    { class: ["banner-card", banner.raw_news && "banner-card--raw"], dataset: { severity: banner.severity, id: banner.banner_id } },
    renderHeader(banner, service),
    h("h3", { class: "banner-card__headline" }, banner.headline),
    !banner.raw_news && banner.for_you && renderForYou(banner.for_you),
    !banner.raw_news && banner.caveat && renderCaveat(),
    !banner.raw_news && renderSources(banner.sources),
    renderActions(banner, service),
  );
  return card;
}

function renderHeader(banner, service) {
  return h(
    "div",
    { class: "banner-card__header" },
    h("span", { class: ["severity-chip", banner.raw_news && "severity-chip--news"] }, banner.raw_news ? "Новость" : banner.label),
    h("span", { class: "banner-card__expires" }, formatExpiresIn(banner.expires_at)),
    h(
      "button",
      {
        class: "banner-card__close",
        type: "button",
        "aria-label": "Не про меня",
        title: "Не про меня",
        onClick: () => service.suppress(banner.banner_id),
      },
      icon("close-14", 14),
    ),
  );
}

function renderForYou(text) {
  return h("div", { class: "for-you" }, h("span", { class: "for-you__label" }, "Для вас"), h("p", { class: "for-you__text" }, text));
}

export function renderCaveat(text = "Уверенность средняя — проверьте источник") {
  return h("div", { class: "caveat" }, icon("caveat", 14), h("span", {}, text));
}

/** @param {import("../model.js").SourceRef[]} sources */
export function renderSources(sources) {
  if (!sources?.length) return null;
  return h(
    "div",
    { class: "sources" },
    sources.map((source) =>
      h(
        "a",
        { class: "source-chip", href: source.url, target: "_blank", rel: "noopener noreferrer", title: source.url },
        icon("link", 11),
        source.title,
      ),
    ),
  );
}

function renderActions(banner, service) {
  const primary = banner.raw_news
    ? h(
        "a",
        {
          class: "chip-button",
          href: banner.sources[0]?.url ?? "#",
          target: "_blank",
          rel: "noopener noreferrer",
          onClick: () => service.markSeen(banner.banner_id),
        },
        "Читать источник",
      )
    : h("button", { class: "chip-button", type: "button", onClick: () => service.discuss(banner) }, "Подробнее");

  return h(
    "div",
    { class: "banner-card__actions" },
    primary,
    h("button", { class: "text-button", type: "button", onClick: () => service.suppress(banner.banner_id) }, "Не про меня"),
    h(
      "button",
      { class: "text-button", type: "button", onClick: () => service.dismiss(banner.banner_id) },
      icon("eye-off", 13),
      "Скрыть баннер",
    ),
  );
}
