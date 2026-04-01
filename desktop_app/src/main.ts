import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import appIcon from "./app-icon.png";

type HealthStatus = {
  appName: string;
  version: string;
  os: string;
  arch: string;
  backendMode: string;
  ready: boolean;
};

type TaskStub = {
  id: string;
  kind: string;
  prompt: string;
  status: string;
  createdAt: string;
  logPath?: string;
  outputRoot?: string;
  pid?: number;
  resultPath?: string | null;
  resultKind?: string | null;
  assessmentComplete?: boolean | null;
  assessmentConfidence?: number | null;
  modelMode?: string | null;
  modelLabel?: string | null;
};

type AppMode = "xhs" | "chatbots";
type XhsModelMode = "cloud" | "local9b";

type State = {
  health: HealthStatus | null;
  healthError: string;
  loadingHealth: boolean;
  launchingTask: boolean;
  prompt: string;
  recentTasks: TaskStub[];
  launchError: string;
  mode: AppMode;
  chatbotsLaunching: boolean;
  chatbotsError: string;
  chatbotsQuestion: string;
  chatbotsResult: TaskStub | null;
  xhsModelMode: XhsModelMode;
};

const state: State = {
  health: null,
  healthError: "",
  loadingHealth: false,
  launchingTask: false,
  prompt: "",
  recentTasks: [],
  launchError: "",
  mode: "chatbots",
  chatbotsLaunching: false,
  chatbotsError: "",
  chatbotsQuestion: "",
  chatbotsResult: null,
  xhsModelMode: "cloud",
};

const xhsPresets = [
  "研究护肤干货",
  "研究露营",
  "拆解 https://www.xiaohongshu.com/user/profile/665e81660000000003033638",
];

function render() {
  const app = document.querySelector("#app");
  if (!(app instanceof HTMLElement)) return;

  const healthy = state.health?.ready && !state.healthError;

  app.innerHTML = `
    <main class="shell">
      <header class="topbar">
        <div class="brand">
          <img class="brand-mark" src="${appIcon}" alt="ClawVision app icon" />
          <span>ClawVision</span>
        </div>

        <button id="status-pill" class="status-pill ${healthy ? "ready" : "idle"}">
          <span class="status-dot"></span>
          ${healthy ? "Ready" : "Check runtime"}
        </button>
      </header>

      <nav class="mode-tabs">
        <button class="mode-tab ${state.mode === "chatbots" ? "active" : ""}" data-mode="chatbots">
          Ask All Chatbots
        </button>
        <button class="mode-tab ${state.mode === "xhs" ? "active" : ""}" data-mode="xhs">
          XHS Research
        </button>
      </nav>

      ${state.mode === "chatbots" ? renderChatbotsMode() : renderXhsMode()}
    </main>
  `;

  bindCommonEvents(app);

  if (state.mode === "chatbots") {
    bindChatbotsEvents(app);
  } else {
    bindXhsEvents(app);
  }
}

function renderChatbotsMode(): string {
  const launchDisabled = state.chatbotsLaunching || !state.chatbotsQuestion.trim();

  return `
    <section class="hero">
      <div class="composer-wrap">
        <h1>Ask ChatGPT, Gemini &amp; Claude</h1>

        <form class="composer chatbot-composer-simple" id="chatbots-form">
          <textarea
            id="chatbots-input"
            rows="4"
            aria-label="Ask ChatGPT, Gemini, and Claude"
          >${escapeHtml(state.chatbotsQuestion)}</textarea>

          <div class="composer-footer">
            <button id="ask-all" type="submit" class="start-button ask-all-button" ${launchDisabled ? "disabled" : ""}>
              ${state.chatbotsLaunching ? "Opening..." : "Ask All"}
            </button>
          </div>
        </form>

        ${
          state.chatbotsError
            ? `<p class="inline-error">${escapeHtml(state.chatbotsError)}</p>`
            : ""
        }
      </div>
    </section>
  `;
}

function renderXhsMode(): string {
  const launchDisabled = state.launchingTask || !state.prompt.trim();

  return `
    <section class="hero ${state.recentTasks.length ? "hero-compact" : ""}">
      <div class="composer-wrap">
        <h1>What should ClawVision do?</h1>

        <div class="composer">
          <div class="xhs-model-tabs">
            <button class="model-tab ${state.xhsModelMode === "cloud" ? "active" : ""}" data-xhs-model="cloud">
              Cloud Claude Sonnet
            </button>
            <button class="model-tab ${state.xhsModelMode === "local9b" ? "active" : ""}" data-xhs-model="local9b">
              Local Qwen 3.5 9B
            </button>
          </div>

          <textarea
            id="task-input"
            rows="5"
            placeholder="Describe a task..."
          >${escapeHtml(state.prompt)}</textarea>

          <div class="composer-actions">
            <div class="preset-row">
              ${xhsPresets
                .map(
                  (preset) =>
                    `<button class="preset" data-preset="${escapeHtmlAttr(preset)}">${escapeHtml(
                      preset,
                    )}</button>`,
                )
                .join("")}
            </div>

            <button id="start-task" class="start-button" ${launchDisabled ? "disabled" : ""}>
              ${state.launchingTask ? "Starting..." : "Start"}
            </button>
          </div>
        </div>

        ${
          state.launchError
            ? `<p class="inline-error">${escapeHtml(state.launchError)}</p>`
            : ""
        }
      </div>
    </section>

    ${
      state.recentTasks.length
        ? `
          <section class="recent">
            ${state.recentTasks
              .map(
                (task) => `
                  <article class="task-card">
                    <div class="task-meta">
                      <span class="task-status ${task.status === "running" ? "status-running" : "status-done"}">${escapeHtml(task.status.toUpperCase())}</span>
                      <span>${escapeHtml(task.id)}</span>
                      ${task.modelLabel ? `<span class="task-model-pill">${escapeHtml(task.modelLabel)}</span>` : ""}
                      ${task.status === "running" ? `<button class="stop-btn" data-task-id="${escapeHtmlAttr(task.id)}">Stop</button>` : ""}
                    </div>
                    <p>${escapeHtml(task.prompt)}</p>
                    ${renderTaskOutcome(task)}
                  </article>
                `,
              )
              .join("")}
          </section>
        `
        : ""
    }
  `;
}

function bindCommonEvents(app: HTMLElement) {
  app.querySelector<HTMLButtonElement>("#status-pill")?.addEventListener("click", () => {
    void refreshHealth();
  });

  for (const button of app.querySelectorAll<HTMLButtonElement>("[data-mode]")) {
    button.addEventListener("click", () => {
      state.mode = button.dataset.mode as AppMode;
      render();
    });
  }
}

function bindChatbotsEvents(app: HTMLElement) {
  app.querySelector<HTMLFormElement>("#chatbots-form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    void askChatbots();
  });

  const input = app.querySelector<HTMLTextAreaElement>("#chatbots-input");
  input?.addEventListener("input", (event) => {
    state.chatbotsQuestion = (event.target as HTMLTextAreaElement).value;
    syncAskButton();
  });
  input?.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      void askChatbots();
    }
  });

  app.querySelector<HTMLButtonElement>("#ask-all")?.addEventListener("click", () => {
    void askChatbots();
  });
}

function bindXhsEvents(app: HTMLElement) {
  const input = app.querySelector<HTMLTextAreaElement>("#task-input");
  input?.addEventListener("input", (event) => {
    state.prompt = (event.target as HTMLTextAreaElement).value;
    syncStartButton();
  });
  input?.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      void startTask();
    }
  });

  app.querySelector<HTMLButtonElement>("#start-task")?.addEventListener("click", () => {
    void startTask();
  });

  for (const button of app.querySelectorAll<HTMLButtonElement>("[data-preset]")) {
    button.addEventListener("click", () => {
      state.prompt = button.dataset.preset || "";
      render();
      app.querySelector<HTMLTextAreaElement>("#task-input")?.focus();
    });
  }

  for (const button of app.querySelectorAll<HTMLButtonElement>("[data-xhs-model]")) {
    button.addEventListener("click", () => {
      state.xhsModelMode = (button.dataset.xhsModel as XhsModelMode) || "cloud";
      render();
    });
  }

  for (const button of app.querySelectorAll<HTMLButtonElement>(".stop-btn")) {
    button.addEventListener("click", async () => {
      const taskId = button.dataset.taskId;
      if (!taskId) return;
      button.disabled = true;
      button.textContent = "Stopping...";
      try {
        await invoke("stop_task", { taskId });
        const task = state.recentTasks.find((t) => t.id === taskId);
        if (task) task.status = "stopped";
      } catch (error) {
        console.warn("stop_task failed:", error);
      }
      render();
    });
  }

  for (const button of app.querySelectorAll<HTMLButtonElement>(".reveal-btn")) {
    button.addEventListener("click", async () => {
      const path = button.dataset.path;
      if (!path) return;
      try {
        await invoke("reveal_path", { path });
      } catch (error) {
        console.warn("reveal_path failed:", error);
      }
    });
  }
}

function syncStartButton() {
  const button = document.querySelector<HTMLButtonElement>("#start-task");
  if (!button) return;
  button.disabled = state.launchingTask || !state.prompt.trim();
}

function syncAskButton() {
  const button = document.querySelector<HTMLButtonElement>("#ask-all");
  if (!button) return;
  button.disabled = state.chatbotsLaunching || !state.chatbotsQuestion.trim();
}

async function refreshHealth() {
  state.loadingHealth = true;
  state.healthError = "";
  render();

  try {
    state.health = await invoke<HealthStatus>("app_health");
  } catch (error) {
    state.healthError = String(error);
  } finally {
    state.loadingHealth = false;
    render();
  }
}

let taskPollTimer: ReturnType<typeof setInterval> | null = null;

function startTaskPolling() {
  if (taskPollTimer) return;
  taskPollTimer = setInterval(async () => {
    try {
      const tasks = await invoke<TaskStub[]>("check_task_status");
      const taskMap = new Map(tasks.map((t) => [t.id, t]));
      let changed = false;
      for (const task of state.recentTasks) {
        const latest = taskMap.get(task.id);
        if (latest) {
          const before = JSON.stringify(task);
          Object.assign(task, latest);
          if (JSON.stringify(task) !== before) {
            changed = true;
          }
        }
      }
      if (changed) render();
      // Stop polling when no tasks are running
      if (!state.recentTasks.some((t) => t.status === "running")) {
        clearInterval(taskPollTimer!);
        taskPollTimer = null;
      }
    } catch {}
  }, 2000);
}

async function startTask() {
  if (!state.prompt.trim() || state.launchingTask) return;

  state.launchingTask = true;
  state.launchError = "";
  render();

  try {
    const task = await invoke<TaskStub>("start_task", {
      prompt: state.prompt.trim(),
      modelMode: state.xhsModelMode,
    });
    state.recentTasks = [task, ...state.recentTasks].slice(0, 4);
    state.prompt = "";
    startTaskPolling();
  } catch (error) {
    state.launchError = String(error);
  } finally {
    state.launchingTask = false;
    render();
  }
}

async function askChatbots() {
  if (!state.chatbotsQuestion.trim() || state.chatbotsLaunching) return;

  state.chatbotsLaunching = true;
  state.chatbotsError = "";
  state.chatbotsResult = null;
  render();

  try {
    const task = await invoke<TaskStub>("ask_chatbots", { question: state.chatbotsQuestion.trim() });
    applyChatbotsTask(task);
  } catch (error) {
    state.chatbotsError = String(error);
  } finally {
    state.chatbotsLaunching = false;
    render();
  }
}

function applyChatbotsTask(task: TaskStub) {
  state.mode = "chatbots";
  state.chatbotsResult = task;
  state.chatbotsQuestion = "";
  state.chatbotsError = "";
}

function renderTaskOutcome(task: TaskStub): string {
  const hasAssessment = typeof task.assessmentConfidence === "number";
  const assessment = hasAssessment
    ? `${task.assessmentComplete ? "Complete" : "Incomplete"} • ${Math.round((task.assessmentConfidence || 0) * 100)}% confidence`
    : "";

  const reportPath = task.resultPath?.trim();
  const outputPath = task.outputRoot?.trim();

  if (!assessment && !reportPath && !outputPath) {
    return "";
  }

  return `
    <div class="task-outcome">
      ${assessment ? `<div class="task-assessment ${task.assessmentComplete ? "assessment-complete" : "assessment-incomplete"}">${escapeHtml(assessment)}</div>` : ""}
      ${reportPath ? `
        <div class="task-path-row">
          <span class="task-path-label">Report</span>
          <code>${escapeHtml(reportPath)}</code>
          <button class="reveal-btn" data-path="${escapeHtmlAttr(reportPath)}">Reveal</button>
        </div>
      ` : ""}
      ${outputPath ? `
        <div class="task-path-row">
          <span class="task-path-label">Output</span>
          <code>${escapeHtml(outputPath)}</code>
          <button class="reveal-btn" data-path="${escapeHtmlAttr(outputPath)}">Reveal</button>
        </div>
      ` : ""}
    </div>
  `;
}

async function initializeDesktopHooks() {
  await listen<TaskStub>("chatbots-launch-requested", (event) => {
    applyChatbotsTask(event.payload);
    render();
  });

  try {
    const latest = await invoke<TaskStub | null>("latest_chatbots_task");
    if (latest) {
      applyChatbotsTask(latest);
      render();
    }
  } catch (error) {
    console.warn("latest_chatbots_task failed", error);
  }
}

function escapeHtml(text: string) {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function escapeHtmlAttr(text: string) {
  return escapeHtml(text).replace(/'/g, "&#39;");
}

window.addEventListener("DOMContentLoaded", () => {
  render();
  void refreshHealth();
  void initializeDesktopHooks();
});
