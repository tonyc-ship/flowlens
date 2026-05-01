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

type OnboardingStepId = "welcome" | "permission" | "connect" | "model" | "ready";
type ViewMode = "onboarding" | "app";
type ConnectionPhase = "idle" | "scanning" | "found" | "creating" | "ready" | "error";
type PermissionStatus = "idle" | "opening" | "opened" | "error";
type AuthMode = "oauth" | "key";
type OAuthStatus = "idle" | "waiting" | "connected";
type ModelId = "sonnet" | "gpt4o" | "kimi" | "qwen";

type OnboardingState = {
  stepId: OnboardingStepId;
  permissionStatus: PermissionStatus;
  permissionError: string;
  connectionPhase: ConnectionPhase;
  connectionStarted: boolean;
  connectionError: string;
  discoveryResult: PrototypeCommandResult | null;
  controlledTabResult: PrototypeCommandResult | null;
  selectedModelId: ModelId;
  authMode: AuthMode;
  oauthStatus: OAuthStatus;
  localProgress: number;
  localDownloading: boolean;
};

type State = {
  viewMode: ViewMode;
  health: HealthStatus | null;
  healthError: string;
  runningAction: ActionId | null;
  results: PrototypeCommandResult[];
  error: string;
  starterTask: string;
  onboarding: OnboardingState;
};

type ModelOption = {
  id: ModelId;
  name: string;
  tag: string | null;
  cost: string;
  desc: string;
  kind: "cloud" | "local";
  brand: string;
};

const onboardingStorageKey = "socaiOnboardingComplete";
const obSteps: { id: OnboardingStepId; label: string }[] = [
  { id: "welcome", label: "Welcome" },
  { id: "permission", label: "Permission" },
  { id: "connect", label: "Connect" },
  { id: "model", label: "Model" },
  { id: "ready", label: "Ready" },
];

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

const modelOptions: ModelOption[] = [
  {
    id: "sonnet",
    name: "Anthropic Sonnet",
    tag: "recommended",
    cost: "$3 / 1M input",
    desc: "Best reasoning, strong vision. The default for general research tasks.",
    kind: "cloud",
    brand: "#d97757",
  },
  {
    id: "gpt4o",
    name: "OpenAI GPT-4o",
    tag: null,
    cost: "$2.50 / 1M input",
    desc: "Solid generalist. Slightly faster than Sonnet on short tasks.",
    kind: "cloud",
    brand: "#10a37f",
  },
  {
    id: "kimi",
    name: "Kimi K2",
    tag: "zh-strong",
    cost: "¥12 / 1M input",
    desc: "Tuned for Chinese-language platforms. Excellent on XHS content.",
    kind: "cloud",
    brand: "#5b8def",
  },
  {
    id: "qwen",
    name: "Qwen local MLX",
    tag: "private",
    cost: "free",
    desc: "Runs on your Mac. Slower, but nothing leaves the device.",
    kind: "local",
    brand: "#7c3aed",
  },
];

const sampleNotes = [
  {
    id: "n1",
    title: "三天两夜露营清单 · 新手别买太多",
    author: "山野与风",
    likes: "3.2万",
    cover: "linear-gradient(135deg,#9bb275,#536b3a 70%)",
  },
  {
    id: "n2",
    title: "100元搞定第一次露营 · 真的不夸张",
    author: "周末出逃局",
    likes: "1.8万",
    cover: "linear-gradient(135deg,#d8a85a,#7d5419 70%)",
  },
  {
    id: "n3",
    title: "夫妻档露营 · 4年踩坑总结",
    author: "南山小院",
    likes: "5.6万",
    cover: "linear-gradient(135deg,#b08fc7,#5e3a72 70%)",
  },
];

const starterTasks = [
  {
    icon: "🔍",
    label: "Research a topic on Xiaohongshu",
    hint: "Search, read 3 notes, and write a report",
  },
  {
    icon: "📝",
    label: "Summarize a single note",
    hint: "Paste a URL — extract images, text, and comments",
  },
  {
    icon: "👤",
    label: "Profile an author",
    hint: "Recent posts + engagement signals",
  },
];

const state: State = {
  viewMode: localStorage.getItem(onboardingStorageKey) === "1" ? "app" : "onboarding",
  health: null,
  healthError: "",
  runningAction: null,
  results: [],
  error: "",
  starterTask: "",
  onboarding: {
    stepId: "welcome",
    permissionStatus: "idle",
    permissionError: "",
    connectionPhase: "idle",
    connectionStarted: false,
    connectionError: "",
    discoveryResult: null,
    controlledTabResult: null,
    selectedModelId: "sonnet",
    authMode: "oauth",
    oauthStatus: "idle",
    localProgress: 0,
    localDownloading: false,
  },
};

let localDownloadTimer: number | null = null;

function render() {
  const app = document.querySelector("#app");
  if (!(app instanceof HTMLElement)) return;

  if (state.viewMode === "onboarding") {
    app.innerHTML = renderOnboarding();
  } else {
    app.innerHTML = renderMainApp();
  }

  bindEvents(app);
}

function renderOnboarding(): string {
  const stepIdx = currentStepIndex();
  return `
    <main class="onboarding-shell">
      <section class="onboarding-window" aria-label="Socai setup wizard">
        ${renderOnboardingHeader(stepIdx)}
        <div class="onboarding-body sa-scroll">
          ${renderCurrentOnboardingStep()}
        </div>
        ${renderOnboardingFooter(stepIdx)}
      </section>
    </main>
  `;
}

function renderOnboardingHeader(stepIdx: number): string {
  return `
    <header class="ob-header">
      <div class="brand-lockup">
        ${renderLogo(22)}
        <span>Socai</span>
      </div>
      <div class="ob-stepper" aria-label="Setup progress">
        ${obSteps
          .map((step, index) => {
            const active = index === stepIdx;
            const done = index < stepIdx;
            return `
              <button class="ob-step ${active ? "active" : ""} ${done ? "done" : ""}" data-ob-step="${step.id}">
                <span class="ob-step-dot">${done ? "✓" : index + 1}</span>
                <span>${escapeHtml(step.label)}</span>
              </button>
              ${index < obSteps.length - 1 ? `<span class="ob-step-line ${done ? "done" : ""}"></span>` : ""}
            `;
          })
          .join("")}
      </div>
    </header>
  `;
}

function renderCurrentOnboardingStep(): string {
  switch (state.onboarding.stepId) {
    case "welcome":
      return renderWelcomeStep();
    case "permission":
      return renderPermissionStep();
    case "connect":
      return renderConnectStep();
    case "model":
      return renderModelStep();
    case "ready":
      return renderReadyStep();
  }
}

function renderOnboardingFooter(stepIdx: number): string {
  const isLast = stepIdx === obSteps.length - 1;
  return `
    <footer class="ob-footer">
      <button class="ob-footer-link" data-ob-back ${stepIdx === 0 ? "disabled" : ""}>← Back</button>
      <div class="ob-step-count">Step ${stepIdx + 1} of ${obSteps.length}</div>
      <button class="ob-btn primary" ${isLast ? "data-complete-onboarding" : "data-ob-next"}>
        ${isLast ? "Open Socai" : "Continue"} →
      </button>
    </footer>
  `;
}

function renderWelcomeStep(): string {
  const bullets = [
    "Uses your existing Chrome — no separate browser, no extra social login",
    "Works in a clearly marked 🟢 Socai tab — not your other tabs",
    "Keeps screenshots, reasoning, and reports together for review",
  ];

  return `
    <section class="ob-grid welcome-grid">
      <div class="ob-copy">
        <p class="ob-eyebrow">Welcome to Socai</p>
        <h1>An agent that browses <span>social platforms</span> for you.</h1>
        <p class="ob-lede">
          Give Socai a research task. It opens a controlled tab in your existing Chrome,
          reads through Xiaohongshu, WeChat, or other platforms, and writes you a report —
          using your real login, so it sees what you'd see.
        </p>
        <div class="ob-check-list">
          ${bullets.map((item) => `<div><span>✓</span><p>${escapeHtml(item)}</p></div>`).join("")}
        </div>
        <div class="ob-actions">
          <button class="ob-btn primary" data-ob-next>Get started</button>
          <button class="ob-btn">Watch a 60s demo</button>
        </div>
        <p class="ob-small">Setup takes about 2 minutes · No account required</p>
      </div>
      ${renderHeroDiagram()}
    </section>
  `;
}

function renderHeroDiagram(): string {
  return `
    <div class="hero-diagram" aria-hidden="true">
      <div class="hero-glow"></div>
      <div class="xhs-frame hero-browser">${renderXhsMock("note")}</div>
      <div class="floating-card reasoning-card">
        <span>Reasoning</span>
        Note 2 has 3× comment density. Extract the cost breakdown.
      </div>
      <div class="floating-card tool-card"><i></i>xhs_extract_note()</div>
      <div class="floating-card report-card">
        <span>report.md</span>
        <b></b><em style="width:85%"></em><em></em><em style="width:70%"></em>
        <small>3 notes · 22 imgs · 4m</small>
      </div>
      <div class="mini-note-stack">
        ${sampleNotes
          .map(
            (note, index) =>
              `<div style="background:${note.cover}; transform: translateY(${index * -4}px) rotate(${(index - 1) * 4}deg)"></div>`,
          )
          .join("")}
      </div>
    </div>
  `;
}

function renderPermissionStep(): string {
  return `
    <section class="ob-grid permission-grid">
      <article class="ob-card">
        <p class="ob-eyebrow">Browser permission</p>
        <h2>Let Socai talk to your Chrome.</h2>
        <p class="ob-muted">
          Socai uses Chrome's built-in remote debugging interface to drive the browser.
          This is the same protocol Chrome DevTools uses, and it only works while Socai is open.
        </p>
        <div class="permission-callout">
          <p class="ob-eyebrow">What Socai can do</p>
          ${[
            ["✓", "Read URL, DOM and screenshots of the 🟢 Socai tab", true],
            ["✓", "Click, scroll, and type inside the 🟢 Socai tab", true],
            ["✓", "Open new tabs that it created itself", true],
            ["✕", "Touch your other tabs, history, or saved passwords", false],
            ["✕", "Run anything when Socai is closed", false],
          ]
            .map(
              ([mark, text, ok]) => `
                <div class="permission-row ${ok ? "allowed" : "blocked"}">
                  <span>${mark}</span><p>${escapeHtml(text)}</p>
                </div>`,
            )
            .join("")}
        </div>
        <div class="ob-actions">
          <button class="ob-btn primary" data-open-inspect>
            ${state.onboarding.permissionStatus === "opening" ? "Opening Chrome…" : "Open Chrome settings"}
          </button>
          <button class="ob-btn">Read full security note</button>
        </div>
        ${renderPermissionStatus()}
      </article>
      <article class="ob-card inspect-card">
        <p class="ob-eyebrow">What you'll do in Chrome</p>
        <p class="ob-muted">
          We'll open <code>chrome://inspect</code> and walk you through Chrome's remote-debugging prompt.
        </p>
        ${renderInspectMock()}
        <p class="ob-small">You only need to approve this once. Socai checks the connection every launch.</p>
      </article>
    </section>
  `;
}

function renderPermissionStatus(): string {
  const status = state.onboarding.permissionStatus;
  if (status === "idle") return "";
  if (status === "opened") {
    return `<div class="inline-status success">Chrome settings opened. Approve the prompt, then continue.</div>`;
  }
  if (status === "error") {
    return `<div class="inline-status error">${escapeHtml(state.onboarding.permissionError)}</div>`;
  }
  return `<div class="inline-status neutral">Opening <code>chrome://inspect/#remote-debugging</code>…</div>`;
}

function renderInspectMock(): string {
  return `
    <div class="inspect-mock">
      <div class="fake-browser-bar">
        <i></i><i></i><i></i><span>chrome://inspect/#remote-debugging</span>
      </div>
      <div class="inspect-content">
        <strong>DevTools</strong>
        <small>Devices · Pages · Extensions · Apps · Workers</small>
        <div class="inspect-toggle">
          <b>CLICK THIS</b>
          <div class="toggle-on"><span></span></div>
          <div>
            <strong>Discover network targets</strong>
            <small>Allow Socai to connect at localhost:9222</small>
          </div>
        </div>
        <pre>localhost:9222 <span>✓ allowed</span></pre>
      </div>
    </div>
  `;
}

function renderConnectStep(): string {
  const phase = state.onboarding.connectionPhase;
  const steps = [
    { id: "scanning", label: "Looking for Chrome on port 9222", detail: "Probing localhost:9222 …" },
    { id: "found", label: "Chrome detected", detail: "Default profile · existing tabs untouched" },
    { id: "creating", label: "Creating controlled tab", detail: "Opening a clearly marked 🟢 Socai tab" },
    { id: "ready", label: "Connection ready", detail: "Socai is now talking to Chrome" },
  ];
  const phaseIdx = Math.max(
    -1,
    steps.findIndex((step) => step.id === phase),
  );

  return `
    <section class="ob-grid connect-grid">
      <article class="ob-card">
        <p class="ob-eyebrow">Connecting</p>
        <h2>${phase === "ready" ? "All set." : phase === "error" ? "Connection needs attention." : "Connect to Chrome."}</h2>
        <div class="connect-steps">
          ${steps
            .map((step, index) => {
              const done = phase === "ready" ? index < steps.length - 1 : index < phaseIdx;
              const live = index === phaseIdx && phase !== "ready" && phase !== "error";
              const finished = phase === "ready" && index === steps.length - 1;
              const queued = phaseIdx === -1 || index > phaseIdx;
              return `
                <div class="connect-step ${done || finished ? "done" : ""} ${live ? "live" : ""} ${queued ? "queued" : ""}">
                  <span>${done || finished ? "✓" : live ? renderSpinner() : ""}</span>
                  <div><strong>${escapeHtml(step.label)}</strong><small>${escapeHtml(step.detail)}</small></div>
                </div>
              `;
            })
            .join("")}
        </div>
        ${renderConnectStatus()}
        <div class="ob-actions">
          <button class="ob-btn primary" data-start-connect ${phase === "scanning" || phase === "creating" ? "disabled" : ""}>
            ${phase === "ready" ? "Re-test connection" : state.onboarding.connectionStarted ? "Try again" : "Start connection test"}
          </button>
          <button class="ob-btn" data-open-inspect>Troubleshoot</button>
        </div>
      </article>
      <article class="ob-card chrome-create-card">
        <p class="ob-eyebrow">What's happening in Chrome</p>
        ${renderChromeTabMock(phaseIdx, phase)}
        <div class="phase-hint ${phase === "ready" ? "success" : phase === "error" ? "error" : ""}">
          <strong>${connectHintTitle(phase)}</strong>
          <p>${connectHintBody(phase)}</p>
        </div>
      </article>
    </section>
  `;
}

function renderConnectStatus(): string {
  const phase = state.onboarding.connectionPhase;
  if (phase === "ready") {
    return `
      <div class="connect-success">
        <span>✓</span>
        <div><strong>Chrome connected</strong><p>You can continue to model setup.</p></div>
      </div>
    `;
  }
  if (phase === "error") {
    return `
      <div class="inline-status error">
        ${escapeHtml(state.onboarding.connectionError || "Could not connect to Chrome. Open Chrome settings and retry.")}
      </div>
    `;
  }
  if (phase === "idle") {
    return `
      <p class="ob-muted connect-copy">
        Click Start connection test after approving Chrome's remote-debugging prompt.
        Socai will discover Chrome and create a marked controlled tab.
      </p>
    `;
  }
  return `<p class="ob-muted connect-copy">If Chrome asks to allow remote debugging, approve it while this test is running.</p>`;
}

function renderChromeTabMock(phaseIdx: number, phase: ConnectionPhase): string {
  const showSocaiTab = phaseIdx >= 2 || phase === "ready";
  return `
    <div class="chrome-mock">
      <div class="fake-browser-bar compact"><i></i><i></i><i></i></div>
      <div class="fake-tabs">
        <span>Inbox – Gmail</span>
        <span>Linear · Sprint 24</span>
        <span>小红书</span>
        <span class="socai-tab ${showSocaiTab ? "visible" : ""} ${phase === "ready" ? "ready" : ""}">
          <i></i>🟢 Socai
        </span>
      </div>
      <div class="fake-page-state">
        ${phase === "ready" ? "ready · awaiting first task" : showSocaiTab ? "loading…" : "(no controlled tab yet)"}
      </div>
    </div>
  `;
}

function connectHintTitle(phase: ConnectionPhase): string {
  if (phase === "idle") return "Ready to test?";
  if (phase === "scanning") return "Scanning your existing Chrome.";
  if (phase === "found") return "Detected your Chrome.";
  if (phase === "creating") return "Creating the Socai tab now…";
  if (phase === "ready") return "Tab is alive and ready for instructions.";
  return "Connection was not completed.";
}

function connectHintBody(phase: ConnectionPhase): string {
  if (phase === "idle") return "Socai will not touch your current tab until you start the test.";
  if (phase === "scanning") return "If this hangs, approve the Chrome prompt or open the troubleshooting link.";
  if (phase === "found") return "Profile detected. Existing tabs stay untouched.";
  if (phase === "creating") return "The controlled tab appears in Chrome with a visible 🟢 marker.";
  if (phase === "ready") return "Click Continue to pick your AI model.";
  return "Open chrome://inspect, approve the prompt, then retry the connection test.";
}

function renderModelStep(): string {
  const selected = selectedModel();
  return `
    <section class="ob-grid model-grid">
      <article class="ob-card model-list-card">
        <p class="ob-eyebrow">Pick your AI model</p>
        <h2>Which model should think for you?</h2>
        <p class="ob-muted">You can change this any time from Settings. Different models can be assigned per task.</p>
        <div class="model-list">
          ${modelOptions.map(renderModelOption).join("")}
        </div>
      </article>
      <article class="ob-card auth-card">
        ${selected.kind === "cloud" ? renderCloudAuth(selected) : renderLocalModel(selected)}
      </article>
    </section>
  `;
}

function renderModelOption(model: ModelOption): string {
  const selected = state.onboarding.selectedModelId === model.id;
  return `
    <button class="model-option ${selected ? "selected" : ""}" data-model-id="${model.id}">
      <span class="model-logo" style="background:${model.brand}">${model.name.charAt(0)}</span>
      <span class="model-main">
        <strong>${escapeHtml(model.name)}</strong>
        ${model.tag ? `<em class="model-tag ${model.tag}">${escapeHtml(model.tag)}</em>` : ""}
        <small>${escapeHtml(model.desc)}</small>
      </span>
      <span class="model-meta"><small>${model.kind === "local" ? "on-device" : "cloud"}</small><strong>${escapeHtml(model.cost)}</strong></span>
    </button>
  `;
}

function renderCloudAuth(model: ModelOption): string {
  const oauth = oauthMeta(model);
  return `
    <p class="ob-eyebrow">Connect to ${escapeHtml(model.name)}</p>
    <p class="ob-muted">Sign in with your existing account, or paste an API key. Credentials should be stored in macOS Keychain when this becomes production auth.</p>
    <div class="segment-control">
      <button class="${state.onboarding.authMode === "oauth" ? "selected" : ""}" data-auth-mode="oauth">Sign in <small>recommended</small></button>
      <button class="${state.onboarding.authMode === "key" ? "selected" : ""}" data-auth-mode="key">API key</button>
    </div>
    ${state.onboarding.authMode === "oauth" ? renderOAuthPanel(model, oauth) : renderKeyPanel(model)}
    <div class="pricing-hint">
      <strong>Typical task cost</strong>
      Researching 3 XHS notes → <b>~$0.04</b> with ${escapeHtml(model.name.split(" ")[0])}${
        state.onboarding.authMode === "oauth" ? " (or counts against your plan quota)" : ""
      }.
    </div>
  `;
}

function oauthMeta(model: ModelOption): { name: string; tagline: string; note: string } {
  if (model.id === "sonnet") {
    return {
      name: "Claude account",
      tagline: "Use your Claude Pro / Team subscription",
      note: "Includes Pro/Team usage limits. No per-request billing.",
    };
  }
  if (model.id === "gpt4o") {
    return {
      name: "ChatGPT account",
      tagline: "Use your ChatGPT Plus / Team subscription",
      note: "Falls back to API billing if usage exceeds plan.",
    };
  }
  if (model.id === "kimi") {
    return {
      name: "Moonshot account",
      tagline: "Sign in with Moonshot",
      note: "Pulls from your Moonshot console balance.",
    };
  }
  return { name: `${model.name} account`, tagline: "Sign in", note: "" };
}

function renderOAuthPanel(model: ModelOption, oauth: { name: string; tagline: string; note: string }): string {
  const status = state.onboarding.oauthStatus;
  return `
    <div class="oauth-panel ${status === "connected" ? "connected" : ""}">
      <div class="oauth-row">
        <span class="model-logo large" style="background:${model.brand}">${model.name.charAt(0)}</span>
        <div><strong>${escapeHtml(oauth.name)}</strong><small>${escapeHtml(oauth.tagline)}</small></div>
        ${status === "connected" ? `<em class="connected-pill"><i></i>connected</em>` : ""}
      </div>
      ${
        status === "connected"
          ? `<div class="oauth-success">Signed in as <strong>alex@gmail.com</strong> · credentials stored in Keychain.</div>`
          : `<button class="ob-btn primary full" data-oauth>${status === "waiting" ? `${renderSpinner(true)} Waiting for browser…` : `Sign in with ${escapeHtml(oauth.name.split(" ")[0])} →`}</button>`
      }
    </div>
    <div class="auth-note">
      <strong>What happens when you sign in</strong>
      We open ${escapeHtml(model.name.split(" ")[0])}'s login page in a separate tab, you authorize Socai, and we receive a token scoped to model access only. ${escapeHtml(oauth.note)}
    </div>
  `;
}

function renderKeyPanel(model: ModelOption): string {
  const placeholder = model.id === "sonnet" ? "sk-ant-…" : "sk-…";
  return `
    <label class="key-label">API key</label>
    <div class="key-input-row">
      <input type="password" placeholder="${placeholder}" />
      <button>Paste</button>
    </div>
    <div class="auth-note">Don't have one? <a href="#">Get an API key from ${escapeHtml(model.name.split(" ")[0])} →</a></div>
    <div class="ob-actions"><button class="ob-btn">Test key</button><button class="ob-btn">Skip — set up later</button></div>
  `;
}

function renderLocalModel(model: ModelOption): string {
  const progress = state.onboarding.localProgress;
  return `
    <p class="ob-eyebrow">On-device model</p>
    <p class="ob-muted">${escapeHtml(model.name)} runs locally via Apple's MLX framework. No API key needed, but you'll download the model once.</p>
    <div class="local-download-card">
      <div><strong>qwen-local-mlx</strong><span>${progress >= 100 ? "ready" : state.onboarding.localDownloading ? `${progress}%` : "model download"}</span></div>
      <div class="progress-track"><i style="width:${progress}%"></i></div>
      <small>${progress >= 100 ? "✓ verified · ready to use" : state.onboarding.localDownloading ? "downloading · simulated" : "not downloaded"}</small>
    </div>
    <div class="ob-actions">
      <button class="ob-btn primary" data-local-download>${progress >= 100 ? "Downloaded ✓" : state.onboarding.localDownloading ? "Downloading…" : "Download model"}</button>
      <button class="ob-btn">Use a smaller version</button>
    </div>
    <div class="privacy-note"><strong>Privacy mode.</strong> With a local model, task content, screenshots, and browser data stay on your machine.</div>
  `;
}

function renderReadyStep(): string {
  const setupRows = [
    ["Browser", setupBrowserSummary()],
    ["Model", `${selectedModel().name} · ${selectedModel().kind === "local" ? "local" : state.onboarding.authMode === "oauth" ? "account sign-in" : "API key"}`],
    ["Storage", "~/Library/Application Support/Socai/"],
  ];
  return `
    <section class="ob-grid ready-grid">
      <div class="ob-copy">
        <p class="ob-eyebrow">You're all set</p>
        <h1>Ready when you are. <span>Try a first task.</span></h1>
        <p class="ob-lede">Pick one of the suggestions on the right, or write your own from the New task screen. Socai will narrate what it does so you can stop or correct it any time.</p>
        <div class="setup-summary">
          <p class="ob-eyebrow">Your setup</p>
          ${setupRows
            .map(
              ([key, value]) => `
                <div><i></i><span>${escapeHtml(key)}</span><strong>${escapeHtml(value)}</strong></div>
              `,
            )
            .join("")}
        </div>
        <div class="ob-actions"><button class="ob-btn primary" data-complete-onboarding>Open Socai →</button><button class="ob-btn">Take a tour first</button></div>
      </div>
      <div class="starter-panel">
        <p class="ob-eyebrow">Try a starter task</p>
        ${starterTasks
          .map(
            (task) => `
              <button class="starter-task" data-starter-task="${escapeHtmlAttr(task.label)}">
                <span>${task.icon}</span>
                <div><strong>${escapeHtml(task.label)}</strong><small>${escapeHtml(task.hint)}</small></div>
                <em>›</em>
              </button>
            `,
          )
          .join("")}
        <div class="did-you-know"><strong>Did you know?</strong><p>Socai also runs as an MCP server, so you can call it from Claude Desktop or any MCP client.</p></div>
      </div>
    </section>
  `;
}

function setupBrowserSummary(): string {
  const status = getJsonStatus(state.onboarding.controlledTabResult?.json ?? null);
  if (status === "controlled_tab_ready") return "Chrome · controlled tab ready";
  if (state.onboarding.connectionPhase === "ready") return "Chrome · CDP connected";
  return "Chrome · setup can be completed later";
}

function renderXhsMock(phase: "feed" | "note" | "search"): string {
  const cards = sampleNotes.concat(sampleNotes).slice(0, 6);
  if (phase === "note") {
    const note = sampleNotes[1];
    return `
      <div class="xhs-mock">
        ${renderXhsChrome("xiaohongshu.com/explore/abc123")}
        <div class="xhs-note-view">
          <div class="xhs-note-cover" style="background:${note.cover}"></div>
          <div class="xhs-note-copy">
            <strong>${escapeHtml(note.title)}</strong>
            <small>@${escapeHtml(note.author)} · 2 days ago</small>
            <p>第一次露营到底要花多少钱？我替你试过了，100块能搞定的就别多花…</p>
            <p>1. 帐篷：拼夕夕双人99元，能用</p>
            <p>2. 防潮垫：充气款比泡沫舒服</p>
          </div>
        </div>
      </div>
    `;
  }
  return `
    <div class="xhs-mock">
      ${renderXhsChrome(phase === "search" ? "xiaohongshu.com/search" : "xiaohongshu.com/explore")}
      <div class="xhs-feed-view">
        ${cards
          .map(
            (note) => `
              <div><b style="background:${note.cover}"></b><strong>${escapeHtml(note.title.slice(0, 18))}…</strong><small>@${escapeHtml(note.author)} · ♥ ${escapeHtml(note.likes)}</small></div>
            `,
          )
          .join("")}
      </div>
    </div>
  `;
}

function renderXhsChrome(url: string): string {
  return `<div class="xhs-chrome"><i></i><i></i><i></i><span>🟢 Socai · ${escapeHtml(url)}</span></div>`;
}

function renderMainApp(): string {
  const latest = state.results[0] || null;
  const connected = latest?.json && getJsonStatus(latest.json) !== "setup_required";

  return `
    <main class="shell">
      <header class="topbar">
        <div>
          <p class="eyebrow">CDP-first social automation prototype</p>
          <h1>Socai Prototype</h1>
        </div>
        <div class="topbar-actions">
          <button id="reset-onboarding" class="secondary-pill" data-reset-onboarding>Run setup again</button>
          <button id="refresh-health" class="status-pill ${state.health?.ready ? "ready" : "idle"}">
            <span class="status-dot"></span>
            ${state.health?.ready ? "Runtime ready" : "Check runtime"}
          </button>
        </div>
      </header>

      ${state.starterTask ? `<section class="notice"><strong>Starter task selected:</strong> ${escapeHtml(state.starterTask)}. The task composer is coming next; use the CDP probes below for now.</section>` : ""}

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
  root.onclick = (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;

    const actionButton = target.closest<HTMLButtonElement>("[data-action]");
    if (actionButton) {
      const actionId = actionButton.dataset.action as ActionId | undefined;
      const action = actions.find((candidate) => candidate.id === actionId);
      if (action) void runAction(action);
      return;
    }

    if (target.closest("#refresh-health")) {
      void loadHealth();
      return;
    }

    if (target.closest("[data-ob-next]")) {
      goOnboardingNext();
      return;
    }

    if (target.closest("[data-ob-back]")) {
      goOnboardingBack();
      return;
    }

    const stepButton = target.closest<HTMLButtonElement>("[data-ob-step]");
    if (stepButton?.dataset.obStep) {
      goToOnboardingStep(stepButton.dataset.obStep as OnboardingStepId);
      return;
    }

    if (target.closest("[data-open-inspect]")) {
      void openChromeInspect();
      return;
    }

    if (target.closest("[data-start-connect]")) {
      void startConnectionTest();
      return;
    }

    const modelButton = target.closest<HTMLButtonElement>("[data-model-id]");
    if (modelButton?.dataset.modelId) {
      state.onboarding.selectedModelId = modelButton.dataset.modelId as ModelId;
      state.onboarding.oauthStatus = "idle";
      render();
      return;
    }

    const authButton = target.closest<HTMLButtonElement>("[data-auth-mode]");
    if (authButton?.dataset.authMode) {
      state.onboarding.authMode = authButton.dataset.authMode as AuthMode;
      render();
      return;
    }

    if (target.closest("[data-oauth]")) {
      simulateOAuth();
      return;
    }

    if (target.closest("[data-local-download]")) {
      startLocalDownload();
      return;
    }

    const starterButton = target.closest<HTMLButtonElement>("[data-starter-task]");
    if (starterButton?.dataset.starterTask) {
      completeOnboarding(starterButton.dataset.starterTask);
      return;
    }

    if (target.closest("[data-complete-onboarding]")) {
      completeOnboarding();
      return;
    }

    if (target.closest("[data-reset-onboarding]")) {
      resetOnboarding();
    }
  };
}

function currentStepIndex(): number {
  return Math.max(
    0,
    obSteps.findIndex((step) => step.id === state.onboarding.stepId),
  );
}

function goOnboardingNext() {
  const stepIdx = currentStepIndex();
  if (stepIdx < obSteps.length - 1) {
    goToOnboardingStep(obSteps[stepIdx + 1].id);
  }
}

function goOnboardingBack() {
  const stepIdx = currentStepIndex();
  if (stepIdx > 0) {
    goToOnboardingStep(obSteps[stepIdx - 1].id);
  }
}

function goToOnboardingStep(stepId: OnboardingStepId) {
  state.onboarding.stepId = stepId;
  render();
}

async function openChromeInspect() {
  state.onboarding.permissionStatus = "opening";
  state.onboarding.permissionError = "";
  render();

  try {
    await invoke<void>("open_chrome_inspect");
    state.onboarding.permissionStatus = "opened";
  } catch (error) {
    state.onboarding.permissionStatus = "error";
    state.onboarding.permissionError = formatError(error);
  }
  render();
}

async function startConnectionTest() {
  if (state.onboarding.connectionPhase === "scanning" || state.onboarding.connectionPhase === "creating") {
    return;
  }

  state.onboarding.connectionStarted = true;
  state.onboarding.connectionPhase = "scanning";
  state.onboarding.connectionError = "";
  state.onboarding.discoveryResult = null;
  state.onboarding.controlledTabResult = null;
  render();

  try {
    const discovery = await invoke<PrototypeCommandResult>("connect_chrome");
    state.onboarding.discoveryResult = discovery;
    const discoveryStatus = getJsonStatus(discovery.json);

    if (!discovery.ok || discoveryStatus !== "cdp_available") {
      throw new Error(
        discoveryStatus === "setup_required"
          ? "Chrome remote debugging is not approved yet. Open Chrome settings, approve the prompt, and retry."
          : discovery.stderr || `Chrome discovery returned status: ${discoveryStatus || "unknown"}`,
      );
    }

    state.onboarding.connectionPhase = "found";
    render();
    await delay(450);

    state.onboarding.connectionPhase = "creating";
    render();

    const controlledTab = await invoke<PrototypeCommandResult>("create_controlled_tab");
    state.onboarding.controlledTabResult = controlledTab;
    const controlledStatus = getJsonStatus(controlledTab.json);

    if (!controlledTab.ok || controlledStatus !== "controlled_tab_ready") {
      throw new Error(
        controlledTab.stderr || `Controlled tab returned status: ${controlledStatus || "unknown"}`,
      );
    }

    state.onboarding.connectionPhase = "ready";
  } catch (error) {
    state.onboarding.connectionPhase = "error";
    state.onboarding.connectionError = formatError(error);
  }

  render();
}

function simulateOAuth() {
  if (state.onboarding.oauthStatus === "waiting") return;
  state.onboarding.oauthStatus = "waiting";
  render();
  window.setTimeout(() => {
    state.onboarding.oauthStatus = "connected";
    render();
  }, 1200);
}

function startLocalDownload() {
  if (state.onboarding.localDownloading || state.onboarding.localProgress >= 100) return;
  state.onboarding.localDownloading = true;
  render();

  if (localDownloadTimer !== null) {
    window.clearInterval(localDownloadTimer);
  }
  localDownloadTimer = window.setInterval(() => {
    state.onboarding.localProgress = Math.min(100, state.onboarding.localProgress + 4);
    if (state.onboarding.localProgress >= 100) {
      state.onboarding.localDownloading = false;
      if (localDownloadTimer !== null) {
        window.clearInterval(localDownloadTimer);
        localDownloadTimer = null;
      }
    }
    render();
  }, 90);
}

function completeOnboarding(starterTask = "") {
  localStorage.setItem(onboardingStorageKey, "1");
  state.viewMode = "app";
  state.starterTask = starterTask;
  render();
  void loadHealth();
}

function resetOnboarding() {
  localStorage.removeItem(onboardingStorageKey);
  state.viewMode = "onboarding";
  state.onboarding.stepId = "welcome";
  render();
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

function selectedModel(): ModelOption {
  return modelOptions.find((model) => model.id === state.onboarding.selectedModelId) || modelOptions[0];
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

function renderLogo(size = 22): string {
  return `
    <svg class="socai-logo" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="10" stroke="#ff3a5c" stroke-width="2"></circle>
      <circle cx="12" cy="12" r="3.5" fill="#ff3a5c"></circle>
      <circle cx="18.5" cy="5.5" r="2.2" fill="#10b981" stroke="#fff" stroke-width="1.2"></circle>
    </svg>
  `;
}

function renderSpinner(light = false): string {
  return `<i class="ob-spinner ${light ? "light" : ""}"></i>`;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
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

if (state.viewMode === "app") {
  void loadHealth();
}
render();
