/**
 * Composition root: picks the API implementation, wires store, service and UI.
 */
import { config } from "./config.js";
import { createHttpClient } from "./api/http-client.js";
import { createMockClient } from "./api/mock-client.js";
import { createStore } from "./state/store.js";
import { INITIAL_ASSISTANT_STATE, createAssistantService } from "./assistant/service.js";
import { createAssistantRail } from "./assistant/ui/rail.js";
import { createAssistantDrawer } from "./assistant/ui/drawer.js";
import { createShell } from "./ui/shell.js";
import { createRouter } from "./ui/router.js";
import { createDemoPanel } from "./ui/demo-panel.js";
import { append } from "./ui/dom.js";
import { LOGIN_PAGE, withPassthroughParams } from "./auth/context.js";

function createApi() {
  if (config.apiMode === "http") return { api: createHttpClient(config), demo: null };
  return createMockClient({ role: config.role });
}

function start() {
  const { api, demo } = createApi();
  const store = createStore(INITIAL_ASSISTANT_STATE);
  const service = createAssistantService({ api, store });

  const shell = createShell();
  const router = createRouter({ shell, deps: { store, service } });

  append(document.getElementById("app"), [
    shell.element,
    createAssistantRail({ store, service }),
    createAssistantDrawer({ store, service }),
    demo && createDemoPanel({ demo, service, router }),
  ]);

  router.start();
  service.loadSignals();
  service.connectStream();
}

if (config.requiresLogin) window.location.replace(withPassthroughParams(LOGIN_PAGE));
else start();
