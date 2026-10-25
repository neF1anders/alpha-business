import { collapseAndRemove, collapsible, h, icon } from "../../ui/dom.js";
import { watch } from "../../state/store.js";
import { CLAIM_TYPE_TITLES } from "../model.js";
import { renderCaveat, renderSources } from "./banner-card.js";

const STARTER_QUESTIONS = [
  "Как ключевая ставка повлияет на мои кредиты?",
  "Что происходит с курсом юаня?",
  "Как дела у моих контрагентов?",
];
const ANSWER_CAVEAT = "Оценка модели, не рекомендация. Проверьте источники.";
const RAW_NEWS_NOTE = "Уверенно ответить не получилось — вот главная новость по теме.";

/** "Чат" tab: messages, typing indicator, follow-up chips. */
export function createChatPanel({ store, service, scrollRoot }) {
  const messages = h("div", { class: "chat", "aria-live": "polite" });
  const typing = collapsible(renderTyping(), "chat__typing is-collapsed");
  const welcome = renderWelcome(service);
  const body = h("div", { class: "drawer__scroll drawer__scroll--chat" }, welcome, messages, typing);

  /** message id -> element */
  const rendered = new Map();

  function scrollToBottom() {
    requestAnimationFrame(() => body.scrollTo({ top: body.scrollHeight, behavior: "smooth" }));
  }

  function sync(chatMessages) {
    const ids = new Set(chatMessages.map((message) => message.id));
    for (const [id, element] of rendered) {
      if (ids.has(id)) continue;
      rendered.delete(id);
      collapseAndRemove(element);
    }

    let appended = false;
    for (const message of chatMessages) {
      if (rendered.has(message.id)) continue;
      if (message.role === "user") collapseStaleFollowUps(messages);
      const element = renderMessage(message, service);
      rendered.set(message.id, element);
      messages.append(element);
      appended = true;
    }

    welcome.classList.toggle("is-collapsed", chatMessages.length > 0);
    if (appended) scrollToBottom();
  }

  watch(store, (state) => state.chat.messages, sync);
  watch(
    store,
    (state) => state.chat.pending,
    (pending) => {
      typing.classList.toggle("is-collapsed", !pending);
      if (pending) scrollToBottom();
    },
  );

  scrollRoot.append(body);
}

function renderWelcome(service) {
  return collapsible(
    h(
      "div",
      { class: "chat-welcome" },
      h("p", { class: "chat-welcome__title" }, "Спросите про ваш бизнес"),
      h("p", { class: "chat-welcome__text" }, "Ассистент отвечает по новостям, аналитике банка и данным вашей компании."),
      h(
        "div",
        { class: "follow-ups" },
        STARTER_QUESTIONS.map((question) =>
          h("button", { class: "follow-up", type: "button", onClick: () => service.ask(question) }, question),
        ),
      ),
    ),
  );
}

function renderMessage(message, service) {
  if (message.role === "user") {
    return h("div", { class: "chat__row chat__row--user enter" }, h("p", { class: "user-bubble" }, message.text));
  }
  if (message.role === "error") {
    return collapsible(
      h(
        "div",
        { class: "answer answer--error", role: "alert" },
        h("p", {}, "Не получилось получить ответ, попробуйте ещё раз."),
        h(
          "button",
          { class: "chip-button chip-button--icon", type: "button", onClick: () => service.retry(message.id) },
          icon("refresh", 14),
          "Повторить",
        ),
      ),
      "enter",
    );
  }
  return h("div", { class: "chat__answer enter" }, renderAnswer(message.answer), renderFollowUps(message.answer.follow_ups, service));
}

/** @param {import("../model.js").ChatAnswer} answer */
function renderAnswer(answer) {
  if (answer.raw_news) return renderRawNews(answer);

  const sourcesByRef = new Map(answer.sources.filter((source) => source.ref_id).map((source) => [source.ref_id, source]));
  let anyLinked = false;
  const claims = answer.claims.map((claim) => {
    const linked = claim.evidence.map((evidence) => sourcesByRef.get(evidence.ref_id)).filter(Boolean);
    if (linked.length) anyLinked = true;
    return renderClaim(claim, linked);
  });

  return h(
    "div",
    { class: "answer" },
    claims,
    // Without ref_id the claim -> source link is unknown: show all sources under the answer.
    !anyLinked && renderSources(answer.sources),
    answer.caveat && h("div", { class: "caveat caveat--plain" }, ANSWER_CAVEAT),
  );
}

function renderClaim(claim, linkedSources) {
  return h(
    "div",
    { class: "claim" },
    h("span", { class: "claim-type", dataset: { type: claim.type } }, CLAIM_TYPE_TITLES[claim.type] ?? claim.type),
    h("p", { class: "claim__text" }, claim.text),
    linkedSources.map((source) =>
      h("a", { class: "claim__source", href: source.url, target: "_blank", rel: "noopener noreferrer" }, `${source.title} ↗`),
    ),
  );
}

function renderRawNews(answer) {
  const lead = answer.sources[0];
  return h(
    "div",
    { class: "answer" },
    renderCaveat(RAW_NEWS_NOTE),
    h(
      "div",
      { class: "claim" },
      h("span", { class: "claim-type", dataset: { type: "news" } }, "Новость"),
      h("p", { class: "claim__text claim__text--strong" }, answer.claims[0]?.text ?? lead?.title ?? ""),
      lead && h("a", { class: "claim__source", href: lead.url, target: "_blank", rel: "noopener noreferrer" }, `Читать источник: ${lead.title} ↗`),
    ),
  );
}

function renderFollowUps(followUps, service) {
  if (!followUps?.length) return null;
  return collapsible(
    h(
      "div",
      { class: "follow-ups" },
      followUps.map((question) =>
        h("button", { class: "follow-up", type: "button", onClick: () => service.ask(question) }, question),
      ),
    ),
    "chat__follow-ups",
  );
}

function collapseStaleFollowUps(container) {
  for (const block of container.querySelectorAll(".chat__follow-ups:not(.is-collapsed)")) {
    block.classList.add("is-collapsed");
  }
}

function renderTyping() {
  return h(
    "div",
    { class: "typing", "aria-label": "Ассистент печатает" },
    h("span", { class: "typing__dot" }),
    h("span", { class: "typing__dot" }),
    h("span", { class: "typing__dot" }),
  );
}
