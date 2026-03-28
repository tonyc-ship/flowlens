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
};

type AppMode = "xhs" | "chatbots";

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
};

const xhsPresets = [
  "研究护肤干货",
  "研究露营",
  "拆解 https://www.xiaohongshu.com/user/profile/665e81660000000003033638",
];

const chatbotPresets = [
  "Explain quantum computing in simple terms",
  "What are the pros and cons of microservices vs monolith?",
  "Write a Python function to merge two sorted lists",
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
        <p class="subtitle">
          Type a question once. ClawVision opens three visible Chrome windows, reuses your existing login state, and enters it for you.
        </p>

        <div class="composer">
          <textarea
            id="chatbots-input"
            rows="4"
            placeholder="Type your question..."
          >${escapeHtml(state.chatbotsQuestion)}</textarea>

          <div class="composer-actions">
            <div class="preset-row">
              ${chatbotPresets
                .map(
                  (preset) =>
                    `<button class="preset" data-chatbot-preset="${escapeHtmlAttr(preset)}">${escapeHtml(
                      preset.length > 40 ? preset.slice(0, 40) + "..." : preset,
                    )}</button>`,
                )
                .join("")}
            </div>

            <button id="ask-all" class="start-button ask-all-button" ${launchDisabled ? "disabled" : ""}>
              ${state.chatbotsLaunching ? "Opening..." : "Ask All"}
            </button>
          </div>
        </div>

        ${
          state.chatbotsError
            ? `<p class="inline-error">${escapeHtml(state.chatbotsError)}</p>`
            : ""
        }

        ${
          state.chatbotsResult
            ? `
              <div class="chatbot-status-row">
                <div class="chatbot-card chatgpt">
                  <div class="chatbot-icon">G</div>
                  <span>ChatGPT</span>
                </div>
                <div class="chatbot-card gemini">
                  <div class="chatbot-icon">G</div>
                  <span>Gemini</span>
                </div>
                <div class="chatbot-card claude">
                  <div class="chatbot-icon">C</div>
                  <span>Claude</span>
                </div>
              </div>
              <p class="chatbot-launched-msg">
                Visible Chrome windows launched.
              </p>
              ${
                state.chatbotsResult.outputRoot
                  ? `<p class="chatbot-output-path">${escapeHtml(state.chatbotsResult.outputRoot)}</p>`
                  : ""
              }
            `
            : ""
        }

        ${
          state.chatbotsLaunching
            ? `
              <div class="chatbot-status-row launching">
                <div class="chatbot-card chatgpt pulsing">
                  <div class="chatbot-icon">G</div>
                  <span>ChatGPT</span>
                </div>
                <div class="chatbot-card gemini pulsing">
                  <div class="chatbot-icon">G</div>
                  <span>Gemini</span>
                </div>
                <div class="chatbot-card claude pulsing">
                  <div class="chatbot-icon">C</div>
                  <span>Claude</span>
                </div>
              </div>
            `
            : ""
        }
      </div>
    </section>
  `;
}

function renderXhsMode(): string {
  const launchDisabled = state.launchingTask || !state.prompt.trim();

  return `
    <section class="hero">
      <div class="composer-wrap">
        <h1>What should ClawVision do?</h1>

        <div class="composer">
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
                      <span>${escapeHtml(task.status)}</span>
                      <span>${escapeHtml(task.id)}</span>
                    </div>
                    <p>${escapeHtml(task.prompt)}</p>
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

  for (const button of app.querySelectorAll<HTMLButtonElement>("[data-chatbot-preset]")) {
    button.addEventListener("click", () => {
      state.chatbotsQuestion = button.dataset.chatbotPreset || "";
      render();
      app.querySelector<HTMLTextAreaElement>("#chatbots-input")?.focus();
    });
  }
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

async function startTask() {
  if (!state.prompt.trim() || state.launchingTask) return;

  state.launchingTask = true;
  state.launchError = "";
  render();

  try {
    const task = await invoke<TaskStub>("start_task", { prompt: state.prompt.trim() });
    state.recentTasks = [task, ...state.recentTasks].slice(0, 4);
    state.prompt = "";
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
