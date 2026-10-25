import { replaceChildren } from "./dom.js";
import { titleOf } from "./shell.js";
import { renderHomePage } from "./pages/home.js";
import {
  renderCounterpartiesPage,
  renderNewPaymentPage,
  renderPaymentsPage,
  renderStubPage,
} from "./pages/action-pages.js";

const PAGES = {
  home: () => renderHomePage(),
  feed: (deps) => renderPaymentsPage(deps),
  "in-work": (deps) => renderPaymentsPage(deps),
  counterparties: (deps) => renderCounterpartiesPage(deps),
  "new-payment": (deps) => renderNewPaymentPage(deps),
};

/** Hash router: #/<route>. Unknown routes render the "outside MVP" stub. */
export function createRouter({ shell, deps }) {
  const currentRoute = () => window.location.hash.replace(/^#\/?/, "") || "home";

  function render() {
    const route = currentRoute();
    const page = PAGES[route]?.(deps) ?? renderStubPage(titleOf(route));
    shell.setActive(route);
    replaceChildren(shell.page, page);
    shell.page.classList.remove("enter");
    void shell.page.offsetWidth;
    shell.page.classList.add("enter");
    window.scrollTo({ top: 0 });
  }

  window.addEventListener("hashchange", render);
  return { start: render, refresh: render };
}
