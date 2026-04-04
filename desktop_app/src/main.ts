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
  watchPath?: string | null;
  watchEvents?: WatchEvent[];
  controlActive?: boolean | null;
};

type WatchEvent = {
  level: string;
  message: string;
  phase?: string;
  detail?: string;
  observation?: string;
  reasoning?: string;
  decision?: string;
  actionName?: string;
};

type AppMode = "xhs" | "chatbots" | "wechat";
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
  wechatConversation: string;
  wechatLaunching: boolean;
  wechatLaunchError: string;
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
  wechatConversation: "",
  wechatLaunching: false,
  wechatLaunchError: "",
};

const xhsPresets = [
  "研究护肤干货",
  "研究露营",
  "拆解 https://www.xiaohongshu.com/user/profile/665e81660000000003033638",
];

const wechatPresets = ["冬虫夏草", ""];

function render() {
  const app = document.querySelector("#app");
  if (!(app instanceof HTMLElement)) return;

  const healthy = state.health?.ready && !state.healthError;

  app.innerHTML = `
    <main class="shell">
      <header class="topbar">
        <div class="brand">
          <img class="brand-mark" src="${appIcon}" alt="FlowLens app icon" />
          <span>FlowLens</span>
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
        <button class="mode-tab ${state.mode === "wechat" ? "active" : ""}" data-mode="wechat">
          WeChat Summary
        </button>
        <button class="mode-tab ${state.mode === "xhs" ? "active" : ""}" data-mode="xhs">
          XHS Research
        </button>
      </nav>

      ${state.mode === "chatbots" ? renderChatbotsMode() : state.mode === "wechat" ? renderWeChatMode() : renderXhsMode()}
    </main>
  `;

  bindCommonEvents(app);

  if (state.mode === "chatbots") {
    bindChatbotsEvents(app);
  } else if (state.mode === "wechat") {
    bindWeChatEvents(app);
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

function renderWeChatMode(): string {
  const launchDisabled = state.wechatLaunching;
  const latestTask = state.recentTasks.find((task) => task.kind === "wechat_chat_summary") || null;
  const promptPreview = state.wechatConversation.trim()
    ? `请总结微信会话 "${state.wechatConversation.trim()}" 的聊天记录`
    : "请总结当前微信已打开会话的聊天记录";

  return `
    <section class="wechat-workbench">
      <div class="composer-wrap">
        <h1>Summarize WeChat Chat</h1>

        <div class="composer wechat-composer">
          <div class="wechat-stack-pill">Local Qwen 2B UI + 9B Agent</div>

          <textarea
            id="wechat-conversation-input"
            rows="3"
            placeholder="Conversation name, or leave empty to reuse the currently open chat..."
          >${escapeHtml(state.wechatConversation)}</textarea>

          <p class="wechat-helper">
            ${escapeHtml(promptPreview)}
          </p>

          <div class="composer-actions">
            <div class="preset-row">
              ${wechatPresets
                .map((preset, index) =>
                  `<button class="preset" data-wechat-preset="${escapeHtmlAttr(preset)}">${
                    preset ? escapeHtml(preset) : index === 1 ? "Current open chat" : "Current chat"
                  }</button>`,
                )
                .join("")}
            </div>

            <button id="start-wechat-task" class="start-button" ${launchDisabled ? "disabled" : ""}>
              ${state.wechatLaunching ? "Starting..." : "Start WeChat Summary"}
            </button>
          </div>

          ${
            state.wechatLaunchError
              ? `<p class="inline-error">${escapeHtml(state.wechatLaunchError)}</p>`
              : ""
          }
        </div>
      </div>

      ${renderWeChatMonitor(latestTask)}
    </section>

    ${
      latestTask
        ? `
          <section class="recent recent-wechat">
            <article class="task-card">
              <div class="task-meta">
                <span class="task-status ${latestTask.status === "running" ? "status-running" : "status-done"}">${escapeHtml(latestTask.status.toUpperCase())}</span>
                <span>${escapeHtml(latestTask.id)}</span>
                ${latestTask.modelLabel ? `<span class="task-model-pill">${escapeHtml(latestTask.modelLabel)}</span>` : ""}
                ${latestTask.status === "running" ? `<button class="stop-btn" data-task-id="${escapeHtmlAttr(latestTask.id)}">Stop</button>` : ""}
              </div>
              <p>${escapeHtml(latestTask.prompt)}</p>
              ${renderTaskOutcome(latestTask)}
            </article>
          </section>
        `
        : ""
    }
  `;
}

function renderXhsMode(): string {
  const xhsTasks = state.recentTasks.filter((task) => task.kind !== "wechat_chat_summary");
  const launchDisabled = state.launchingTask || !state.prompt.trim();

  return `
    <section class="hero ${xhsTasks.length ? "hero-compact" : ""}">
      <div class="composer-wrap">
        <h1>What should FlowLens do?</h1>

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
      xhsTasks.length
        ? `
          <section class="recent">
            ${xhsTasks
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

function bindWeChatEvents(app: HTMLElement) {
  const input = app.querySelector<HTMLTextAreaElement>("#wechat-conversation-input");
  input?.addEventListener("input", (event) => {
    state.wechatConversation = (event.target as HTMLTextAreaElement).value;
    syncWeChatStartButton();
  });
  input?.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      void startWeChatTask();
    }
  });

  app.querySelector<HTMLButtonElement>("#start-wechat-task")?.addEventListener("click", () => {
    void startWeChatTask();
  });

  for (const button of app.querySelectorAll<HTMLButtonElement>("[data-wechat-preset]")) {
    button.addEventListener("click", () => {
      state.wechatConversation = button.dataset.wechatPreset || "";
      render();
      app.querySelector<HTMLTextAreaElement>("#wechat-conversation-input")?.focus();
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

function syncWeChatStartButton() {
  const button = document.querySelector<HTMLButtonElement>("#start-wechat-task");
  if (!button) return;
  button.disabled = state.wechatLaunching;
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

async function startWeChatTask() {
  if (state.wechatLaunching) return;

  state.wechatLaunching = true;
  state.wechatLaunchError = "";
  render();

  const prompt = state.wechatConversation.trim()
    ? `请总结微信会话 "${state.wechatConversation.trim()}" 的聊天记录`
    : "请总结当前微信已打开会话的聊天记录";

  try {
    const task = await invoke<TaskStub>("start_task", {
      prompt,
      modelMode: "local9b",
    });
    state.recentTasks = [task, ...state.recentTasks.filter((item) => item.id !== task.id)].slice(0, 8);
    startTaskPolling();
  } catch (error) {
    state.wechatLaunchError = String(error);
  } finally {
    state.wechatLaunching = false;
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

function renderWeChatMonitor(task: TaskStub | null): string {
  const events = task?.watchEvents || [];
  const caution = task?.controlActive
    ? `<div class="control-banner">FlowLens is controlling WeChat. Do not fight for the mouse or keyboard.</div>`
    : "";
  const body = events.length
    ? events
        .map(
          (event) => `
            <article class="watch-event watch-${escapeHtmlAttr(event.level || "info")}">
              <div class="watch-event-top">
                <span>${escapeHtml((event.phase || event.actionName || event.level || "event").toUpperCase())}</span>
              </div>
              <p>${escapeHtml(event.message || event.detail || event.decision || "")}</p>
              ${event.detail ? `<p class="watch-detail">${escapeHtml(event.detail)}</p>` : ""}
              ${event.decision ? `<p class="watch-detail">${escapeHtml(event.decision)}</p>` : ""}
            </article>
          `,
        )
        .join("")
    : `<p class="watch-empty">Launch a WeChat task to see scrolling, parsing, and summary status here.</p>`;

  return `
    <aside class="watch-panel">
      <div class="watch-panel-top">
        <h2>Run Monitor</h2>
        ${task?.status ? `<span class="watch-status">${escapeHtml(task.status)}</span>` : ""}
      </div>
      ${caution}
      <div class="watch-list">${body}</div>
    </aside>
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
