/**
 * Real BFF client, implements docs/api-contract.md.
 * Same shape as the mock client, so the rest of the app does not know which one it uses.
 */

export class ApiError extends Error {
  constructor(code, message, status = 0) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

const MAX_SSE_FAILURES_BEFORE_POLLING = 3;

export function createHttpClient({ apiBase, companyId, pollIntervalMs }) {
  const root = `${apiBase}/api/companies/${encodeURIComponent(companyId)}`;

  async function request(path, { method = "GET", body } = {}) {
    let response;
    try {
      response = await fetch(`${root}${path}`, {
        method,
        headers: body === undefined ? {} : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (networkError) {
      throw new ApiError("network", networkError.message);
    }
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new ApiError(payload.error ?? "http", payload.message ?? response.statusText, response.status);
    }
    return response.status === 204 ? null : response.json();
  }

  return {
    getSignals: () => request("/signals"),
    getInlineSignal: (page) => request(`/signals/inline?page=${encodeURIComponent(page)}`),
    markSeen: (bannerId) => request(`/signals/${encodeURIComponent(bannerId)}/seen`, { method: "POST" }),
    suppress: (bannerId, reason = "not_relevant") =>
      request(`/signals/${encodeURIComponent(bannerId)}/suppress`, { method: "POST", body: { reason } }),
    dismiss: (bannerId) => request(`/signals/${encodeURIComponent(bannerId)}/dismiss`, { method: "POST" }),
    ask: ({ question, bannerId }) =>
      request("/chat", { method: "POST", body: bannerId ? { question, banner_id: bannerId } : { question } }),
    subscribe: (handlers) => subscribeToStream(`${root}/signals/stream`, handlers, pollIntervalMs),
  };
}

/**
 * SSE with events `banners` (BannerDTO[]) and `invalidate` (banner_id[]).
 * After a reconnect the caller re-reads GET /signals (`onResync`).
 * Falls back to polling when SSE keeps failing.
 */
function subscribeToStream(url, { onBanners, onInvalidate, onResync }, pollIntervalMs) {
  let pollTimer = null;
  let failures = 0;
  let source = null;

  const startPolling = () => {
    if (pollTimer) return;
    pollTimer = setInterval(onResync, pollIntervalMs);
  };

  if (typeof EventSource === "undefined") {
    startPolling();
  } else {
    source = new EventSource(url);
    source.addEventListener("banners", (event) => onBanners(JSON.parse(event.data)));
    source.addEventListener("invalidate", (event) => onInvalidate(JSON.parse(event.data)));
    source.addEventListener("open", () => {
      if (failures > 0) onResync();
      failures = 0;
    });
    source.addEventListener("error", () => {
      failures += 1;
      if (failures >= MAX_SSE_FAILURES_BEFORE_POLLING) {
        source.close();
        startPolling();
      }
    });
  }

  return () => {
    source?.close();
    clearInterval(pollTimer);
  };
}
