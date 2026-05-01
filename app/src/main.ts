import { invoke } from "@tauri-apps/api/core";

type HealthStatus = {
  appName: string;
  version: string;
  os: string;
  arch: string;
  backendMode: string;
  ready: boolean;
};

type ScreenshotArtifact = {
  label: string;
  path: string;
  dataUrl: string;
};

type PrototypeCommandResult = {
  action: string;
  ok: boolean;
  exitCode: number | null;
  stdout: string;
  stderr: string;
  json: unknown | null;
  screenshots: ScreenshotArtifact[];
};

type ActionId =
  | "connectChrome"
  | "listTargets"
  | "createControlledTab"
  | "openXhsProbe"
  | "captureTestScreenshot";

type ActionConfig = {
  id: ActionId;
  label: string;
  command: string;
  description: string;
};

type State = {
  health: HealthStatus | null;
  healthError: string;
  runningAction: ActionId | null;
  results: PrototypeCommandResult[];
  error: string;
};

const actions: ActionConfig[] = [
  {
    id: "connectChrome",
    label: "Connect Chrome",
    command: "connect_chrome",
    description: "Check whether the existing Chrome profile exposes a CDP endpoint.",
  },
  {
    id: "listTargets",
    label: "List Targets",
    command: "list_chrome_targets",
    description: "Connect with cdp-use and call Target.getTargets.",
  },
  {
    id: "createControlledTab",
    label: "Create Controlled Tab",
    command: "create_controlled_tab",
    description: "Create a new tab, mark it with 🟢 Socai, and verify primitives.",
  },
  {
    id: "openXhsProbe",
    label: "Open XHS Probe",
    command: "open_xhs_probe",
    description: "Open Xiaohongshu, scroll, read runtime state, and capture screenshots.",
  },
  {
    id: "captureTestScreenshot",
    label: "Capture Test Screenshot",
    command: "capture_test_screenshot",
    description: "Run the controlled-tab screenshot proof again.",
  },
];

const state: State = {
  health: null,
  healthError: "",
  runningAction: null,
  results: [],
  error: "",
};

function render() {
  const app = document.querySelector("#app");
  if (!(app instanceof HTMLElement)) return;

  const latest = state.results[0] || null;
  const connected = latest?.json && getJsonStatus(latest.json) !== "setup_required";

  app.innerHTML = `
    <main class="shell">
      <header class="topbar">
        <div>
          <p class="eyebrow">CDP-first social automation prototype</p>
          <h1>Socai Prototype</h1>
        </div>
        <button id="refresh-health" class="status-pill ${state.health?.ready ? "ready" : "idle"}">
          <span class="status-dot"></span>
          ${state.health?.ready ? "Runtime ready" : "Check runtime"}
        </button>
      </header>

      <section class="notice">
        <strong>Permission note:</strong>
        Socai connects only after Chrome remote-debugging permission is enabled. If Chrome shows
        <em>Allow remote debugging?</em>, click <strong>Allow</strong> while the current action is running.
      </section>

      <section class="grid">
        <article class="panel controls-panel">
          <div class="panel-heading">
            <p class="eyebrow">Procedure</p>
            <h2>Run one step at a time</h2>
          </div>
          <div class="action-list">
            ${actions.map(renderActionButton).join("")}
          </div>
        </article>

        <article class="panel status-panel">
          <div class="panel-heading">
            <p class="eyebrow">Status</p>
            <h2>${latest ? escapeHtml(resultTitle(latest)) : "Waiting for first action"}</h2>
          </div>
          ${renderHealth()}
          ${state.error ? `<pre class="error-box">${escapeHtml(state.error)}</pre>` : ""}
          ${state.healthError ? `<pre class="error-box">${escapeHtml(state.healthError)}</pre>` : ""}
          ${latest ? renderResultSummary(latest) : renderEmptyState()}
        </article>
      </section>

      ${latest ? renderArtifacts(latest) : ""}
      ${state.results.length ? renderHistory() : ""}

      <footer class="footer">
        <span>Current controlled-browser status: ${connected ? "action returned browser data" : "not connected in UI yet"}</span>
      </footer>
    </main>
  `;

  bindEvents(app);
}

function renderActionButton(action: ActionConfig): string {
  const running = state.runningAction === action.id;
  const disabled = Boolean(state.runningAction);
  return `
    <button class="action-card" data-action="${action.id}" ${disabled ? "disabled" : ""}>
      <span class="action-title">${running ? "Running…" : escapeHtml(action.label)}</span>
      <span class="action-description">${escapeHtml(action.description)}</span>
    </button>
  `;
}

function renderHealth(): string {
  if (!state.health) {
    return `<p class="muted">Runtime health has not been checked yet.</p>`;
  }

  return `
    <dl class="health-grid">
      <div><dt>App</dt><dd>${escapeHtml(state.health.appName)} ${escapeHtml(state.health.version)}</dd></div>
      <div><dt>System</dt><dd>${escapeHtml(state.health.os)} / ${escapeHtml(state.health.arch)}</dd></div>
      <div><dt>Backend</dt><dd>${escapeHtml(state.health.backendMode)}</dd></div>
    </dl>
  `;
}

function renderEmptyState(): string {
  return `
    <div class="empty-state">
      <p>Start with <strong>Connect Chrome</strong>. Then list targets, create a controlled tab, and run the XHS probe.</p>
    </div>
  `;
}

function renderResultSummary(result: PrototypeCommandResult): string {
  const status = getJsonStatus(result.json);
  return `
    <div class="result-summary ${result.ok ? "success" : "failure"}">
      <div><strong>Action:</strong> ${escapeHtml(result.action)}</div>
      <div><strong>Exit:</strong> ${result.exitCode ?? "unknown"}</div>
      <div><strong>Status:</strong> ${escapeHtml(status || (result.ok ? "ok" : "failed"))}</div>
    </div>
    ${result.stderr.trim() ? `<pre class="stderr-box">${escapeHtml(result.stderr.trim())}</pre>` : ""}
  `;
}

function renderArtifacts(result: PrototypeCommandResult): string {
  const screenshots = result.screenshots || [];
  const json = result.json;

  return `
    <section class="artifacts">
      <article class="panel">
        <div class="panel-heading">
          <p class="eyebrow">Screenshots</p>
          <h2>${screenshots.length ? `${screenshots.length} artifact${screenshots.length === 1 ? "" : "s"}` : "No screenshots returned"}</h2>
        </div>
        ${
          screenshots.length
            ? `<div class="screenshot-grid">${screenshots.map(renderScreenshot).join("")}</div>`
            : `<p class="muted">Run Create Controlled Tab or Open XHS Probe to display screenshots.</p>`
        }
      </article>

      <article class="panel">
        <div class="panel-heading">
          <p class="eyebrow">JSON</p>
          <h2>Command result</h2>
        </div>
        <pre class="json-box">${escapeHtml(JSON.stringify(json ?? result.stdout, null, 2))}</pre>
      </article>
    </section>
  `;
}

function renderScreenshot(artifact: ScreenshotArtifact): string {
  return `
    <figure class="screenshot-card">
      <img src="${artifact.dataUrl}" alt="${escapeHtmlAttr(artifact.label)}" />
      <figcaption>
        <strong>${escapeHtml(artifact.label)}</strong>
        <span>${escapeHtml(artifact.path)}</span>
      </figcaption>
    </figure>
  `;
}

function renderHistory(): string {
  return `
    <section class="panel history-panel">
      <div class="panel-heading">
        <p class="eyebrow">History</p>
        <h2>Recent prototype actions</h2>
      </div>
      <div class="history-list">
        ${state.results
          .map(
            (result) => `
              <button class="history-item" data-history-action="${escapeHtmlAttr(result.action)}">
                <span>${escapeHtml(resultTitle(result))}</span>
                <small>${escapeHtml(getJsonStatus(result.json) || (result.ok ? "ok" : "failed"))}</small>
              </button>
            `,
          )
          .join("")}
      </div>
    </section>
  `;
}

function bindEvents(root: HTMLElement) {
  root.querySelector("#refresh-health")?.addEventListener("click", () => {
    void loadHealth();
  });

  root.querySelectorAll<HTMLButtonElement>("[data-action]").forEach((button) => {
    button.addEventListener("click", () => {
      const actionId = button.dataset.action as ActionId | undefined;
      const action = actions.find((candidate) => candidate.id === actionId);
      if (action) void runAction(action);
    });
  });
}

async function loadHealth() {
  state.healthError = "";
  try {
    state.health = await invoke<HealthStatus>("app_health");
  } catch (error) {
    state.healthError = formatError(error);
  }
  render();
}

async function runAction(action: ActionConfig) {
  state.runningAction = action.id;
  state.error = "";
  render();

  try {
    const result = await invoke<PrototypeCommandResult>(action.command);
    state.results = [result, ...state.results].slice(0, 8);
  } catch (error) {
    state.error = formatError(error);
  } finally {
    state.runningAction = null;
    render();
  }
}

function resultTitle(result: PrototypeCommandResult): string {
  const status = getJsonStatus(result.json);
  return `${result.action}${status ? ` — ${status}` : ""}`;
}

function getJsonStatus(json: unknown): string {
  if (!json || typeof json !== "object") return "";
  const value = (json as Record<string, unknown>).status;
  return typeof value === "string" ? value : "";
}

function formatError(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  return JSON.stringify(error, null, 2);
}

function escapeHtml(value: unknown): string {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeHtmlAttr(value: unknown): string {
  return escapeHtml(value).replaceAll("`", "&#096;");
}

void loadHealth();
render();
