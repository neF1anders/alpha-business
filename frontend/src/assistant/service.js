/**
 * Assistant use cases: load signals, react to push, hide/suppress, chat.
 * UI calls these methods and renders from the store; it never talks to the API directly.
 */

export const INITIAL_ASSISTANT_STATE = {
  signals: { status: "idle", items: [], counts: { attention: 0, watch: 0, opportunity: 0 } },
  drawer: { open: false, tab: "banners", filter: "all" },
  chat: { messages: [], pending: false },
  /** Timestamp of the last urgent push; the rail pulses when it changes. */
  urgentPushAt: 0,
};

const DISCUSS_QUESTION = "Расскажите подробнее, что это значит для меня";

export function createAssistantService({ api, store }) {
  const seenIds = new Set();
  let messageSeq = 0;
  let signalsRequest = 0;

  const setSignals = (producer) => store.update((state) => ({ signals: { ...state.signals, ...producer(state.signals) } }));
  const setDrawer = (patch) => store.update((state) => ({ drawer: { ...state.drawer, ...patch } }));
  const setChat = (producer) => store.update((state) => ({ chat: { ...state.chat, ...producer(state.chat) } }));

  async function loadSignals({ silent = false } = {}) {
    const requestId = ++signalsRequest;
    if (!silent) setSignals(() => ({ status: "loading" }));
    try {
      const response = await api.getSignals();
      if (requestId !== signalsRequest) return;
      setSignals(() => ({ status: "ready", items: response.items, counts: normalizeCounts(response.counts) }));
    } catch (error) {
      if (requestId !== signalsRequest) return;
      console.warn("[assistant] signals failed", error);
      // A failed background resync keeps what the user already sees.
      if (!silent) setSignals(() => ({ status: "error" }));
    }
  }

  function removeLocally(bannerId) {
    const removed = store.get().signals.items.find((item) => item.banner_id === bannerId);
    if (!removed) return null;
    setSignals((signals) => ({
      items: signals.items.filter((item) => item.banner_id !== bannerId),
      counts: { ...signals.counts, [removed.severity]: Math.max(0, signals.counts[removed.severity] - 1) },
    }));
    return removed;
  }

  function restoreLocally(banner) {
    setSignals((signals) => ({
      items: [banner, ...signals.items],
      counts: { ...signals.counts, [banner.severity]: signals.counts[banner.severity] + 1 },
    }));
  }

  /** Optimistic removal: the card disappears at once, comes back if the backend refuses. */
  async function removeBanner(bannerId, request) {
    const removed = removeLocally(bannerId);
    try {
      await request();
    } catch (error) {
      console.warn("[assistant] hide failed", error);
      if (removed) restoreLocally(removed);
    }
  }

  function addPushed(banners) {
    const known = new Set(store.get().signals.items.map((item) => item.banner_id));
    const fresh = banners.filter((banner) => !known.has(banner.banner_id));
    if (fresh.length === 0) return;
    setSignals((signals) => {
      const counts = { ...signals.counts };
      for (const banner of fresh) counts[banner.severity] += 1;
      return { items: [...fresh, ...signals.items], counts };
    });
    if (fresh.some((banner) => banner.severity === "attention")) {
      store.update(() => ({ urgentPushAt: Date.now() }));
    }
  }

  function addMessage(message) {
    const withId = { id: `m${++messageSeq}`, ...message };
    setChat((chat) => ({ messages: [...chat.messages, withId] }));
    return withId;
  }

  async function requestAnswer(request) {
    setChat(() => ({ pending: true }));
    try {
      const answer = await api.ask(request);
      addMessage({ role: "assistant", answer });
    } catch (error) {
      console.warn("[assistant] chat failed", error);
      addMessage({ role: "error", retry: request });
    } finally {
      setChat(() => ({ pending: false }));
    }
  }

  function ask(question, { bannerId, displayText } = {}) {
    const text = question.trim();
    if (!text || store.get().chat.pending) return;
    addMessage({ role: "user", text: displayText ?? text });
    requestAnswer({ question: text, bannerId });
  }

  return {
    loadSignals,

    connectStream() {
      return api.subscribe({
        onBanners: addPushed,
        onInvalidate: (bannerIds) => bannerIds.forEach(removeLocally),
        onResync: () => loadSignals({ silent: store.get().signals.status === "ready" }),
      });
    },

    openDrawer({ tab = "banners", filter } = {}) {
      setDrawer({ open: true, tab, ...(filter ? { filter } : {}) });
    },
    closeDrawer: () => setDrawer({ open: false }),
    setTab: (tab) => setDrawer({ tab }),
    setFilter: (filter) => setDrawer({ filter }),

    suppress: (bannerId) => removeBanner(bannerId, () => api.suppress(bannerId, "not_relevant")),
    dismiss: (bannerId) => removeBanner(bannerId, () => api.dismiss(bannerId)),

    markSeen(bannerId) {
      if (seenIds.has(bannerId)) return;
      seenIds.add(bannerId);
      api.markSeen(bannerId).catch((error) => console.warn("[assistant] seen failed", error));
    },

    getInlineSignal: (page) => api.getInlineSignal(page),

    ask,

    retry(messageId) {
      const message = store.get().chat.messages.find((item) => item.id === messageId);
      if (!message?.retry) return;
      setChat((chat) => ({ messages: chat.messages.filter((item) => item.id !== messageId) }));
      // The user bubble is already on screen, so only the request is repeated.
      requestAnswer(message.retry);
    },

    /** "Подробнее" / "Обсудить": opens chat and asks about the banner with its cluster as context. */
    discuss(banner) {
      setDrawer({ open: true, tab: "chat" });
      ask(DISCUSS_QUESTION, { bannerId: banner.banner_id, displayText: `Подробнее: «${banner.headline}»` });
    },
  };
}

function normalizeCounts(counts = {}) {
  return { attention: counts.attention ?? 0, watch: counts.watch ?? 0, opportunity: counts.opportunity ?? 0 };
}
