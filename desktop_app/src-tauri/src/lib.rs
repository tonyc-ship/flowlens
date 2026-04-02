use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::env;
extern crate libc;
use std::ffi::OsString;
use std::fs::{self, File};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};
use tauri::{AppHandle, Emitter, Manager, State};
use tauri_plugin_deep_link::DeepLinkExt;
use url::Url;

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct HealthStatus {
    app_name: &'static str,
    version: &'static str,
    os: &'static str,
    arch: &'static str,
    backend_mode: &'static str,
    ready: bool,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct WatchEventStub {
    level: String,
    message: String,
    #[serde(default)]
    phase: String,
    #[serde(default)]
    detail: String,
    #[serde(default)]
    observation: String,
    #[serde(default)]
    reasoning: String,
    #[serde(default)]
    decision: String,
    #[serde(default)]
    action_name: String,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct TaskStub {
    id: String,
    kind: String,
    prompt: String,
    status: String,
    created_at: String,
    log_path: String,
    output_root: String,
    pid: u32,
    #[serde(default)]
    result_path: Option<String>,
    #[serde(default)]
    result_kind: Option<String>,
    #[serde(default)]
    assessment_complete: Option<bool>,
    #[serde(default)]
    assessment_confidence: Option<f64>,
    #[serde(default)]
    model_mode: Option<String>,
    #[serde(default)]
    model_label: Option<String>,
    #[serde(default)]
    watch_path: Option<String>,
    #[serde(default)]
    watch_events: Vec<WatchEventStub>,
    #[serde(default)]
    control_active: Option<bool>,
}

#[derive(Default)]
struct LatestChatbotsTask(Mutex<Option<TaskStub>>);

#[derive(Default)]
struct RunningTasks(Mutex<Vec<TaskStub>>);

#[derive(Default)]
struct ChatbotsCompanionState(Mutex<Option<ChatbotsCompanionHandle>>);

struct ChatbotsCompanionHandle {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
}

enum LauncherKind {
    Binary,
    Python,
}

struct RuntimePaths {
    workdir: PathBuf,
    output_root: PathBuf,
    launcher: OsString,
    launcher_kind: LauncherKind,
}

#[tauri::command]
fn app_health() -> HealthStatus {
    HealthStatus {
        app_name: "ClawVision Desktop",
        version: env!("CARGO_PKG_VERSION"),
        os: std::env::consts::OS,
        arch: std::env::consts::ARCH,
        backend_mode: "local companion runtime",
        ready: true,
    }
}

fn repo_root() -> Result<PathBuf, String> {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .map_err(|err| format!("Failed to resolve repo root: {err}"))
}

fn dev_runtime_root() -> Option<PathBuf> {
    let candidate = Path::new(env!("CARGO_MANIFEST_DIR")).join("../runtime_bundle");
    if candidate.join("clawvision").exists() {
        return candidate.canonicalize().ok();
    }
    None
}

fn env_nonempty(name: &str) -> Option<OsString> {
    env::var_os(name).filter(|value| !value.is_empty())
}

fn resolve_python(primary_root: &Path, fallback_root: Option<&Path>) -> OsString {
    if let Some(python) = env_nonempty("CLAWVISION_PYTHON") {
        return python;
    }

    let primary_venv = primary_root.join(".venv").join("bin").join("python");
    if primary_venv.exists() {
        return primary_venv.into_os_string();
    }

    if let Some(root) = fallback_root {
        let fallback_venv = root.join(".venv").join("bin").join("python");
        if fallback_venv.exists() {
            return fallback_venv.into_os_string();
        }
    }

    OsString::from("python3")
}

fn resolve_runtime(app: &AppHandle) -> Result<RuntimePaths, String> {
    let dev_repo = repo_root().ok();

    if let Ok(resource_dir) = app.path().resource_dir() {
        let bundled_root = if resource_dir
            .join("runtime_bundle")
            .join("clawvision")
            .exists()
        {
            Some(resource_dir.join("runtime_bundle"))
        } else if resource_dir
            .join("_up_")
            .join("runtime_bundle")
            .join("clawvision")
            .exists()
        {
            Some(resource_dir.join("_up_").join("runtime_bundle"))
        } else {
            None
        };

        if let Some(bundled_root) = bundled_root {
            let bundled_binary = bundled_root.join("bin").join("clawvision");
            let (launcher, launcher_kind) =
                if let Some(executable) = env_nonempty("CLAWVISION_EXECUTABLE") {
                    (executable, LauncherKind::Binary)
                } else if bundled_binary.exists() {
                    (bundled_binary.into_os_string(), LauncherKind::Binary)
                } else {
                    (
                        resolve_python(&bundled_root, dev_repo.as_deref()),
                        LauncherKind::Python,
                    )
                };
            let output_root = app
                .path()
                .app_data_dir()
                .map_err(|err| format!("Failed to resolve app data dir: {err}"))?
                .join("task_runs");
            return Ok(RuntimePaths {
                workdir: bundled_root,
                output_root,
                launcher,
                launcher_kind,
            });
        }
    }

    if let Some(runtime_root) = dev_runtime_root() {
        let runtime_binary = runtime_root.join("bin").join("clawvision");
        let (launcher, launcher_kind) =
            if let Some(executable) = env_nonempty("CLAWVISION_EXECUTABLE") {
                (executable, LauncherKind::Binary)
            } else if runtime_binary.exists() {
                (runtime_binary.into_os_string(), LauncherKind::Binary)
            } else {
                (
                    resolve_python(&runtime_root, dev_repo.as_deref()),
                    LauncherKind::Python,
                )
            };
        let output_root = dev_repo
            .clone()
            .unwrap_or(runtime_root.clone())
            .join("task_runs");
        return Ok(RuntimePaths {
            workdir: runtime_root,
            output_root,
            launcher,
            launcher_kind,
        });
    }

    let repo_root = dev_repo.ok_or_else(|| {
        "Could not resolve a ClawVision runtime. Expected either bundled runtime resources or the repo checkout.".to_string()
    })?;

    Ok(RuntimePaths {
        workdir: repo_root.clone(),
        output_root: repo_root.join("task_runs"),
        launcher: resolve_python(&repo_root, None),
        launcher_kind: LauncherKind::Python,
    })
}

fn build_pythonpath(root: &Path) -> Result<OsString, String> {
    let mut paths = vec![root.to_path_buf()];
    if let Some(existing) = env::var_os("PYTHONPATH") {
        paths.extend(env::split_paths(&existing));
    }
    env::join_paths(paths).map_err(|err| format!("Failed to build PYTHONPATH: {err}"))
}

fn make_task_stub(
    id: String,
    kind: &str,
    prompt: &str,
    created_at: String,
    log_path: PathBuf,
    output_root: PathBuf,
    pid: u32,
    model_mode: Option<String>,
    model_label: Option<String>,
) -> TaskStub {
    TaskStub {
        id,
        kind: kind.to_string(),
        prompt: prompt.to_string(),
        status: "running".to_string(),
        created_at,
        log_path: log_path.to_string_lossy().into_owned(),
        output_root: output_root.to_string_lossy().into_owned(),
        pid,
        result_path: None,
        result_kind: None,
        assessment_complete: None,
        assessment_confidence: None,
        model_mode,
        model_label,
        watch_path: None,
        watch_events: vec![],
        control_active: None,
    }
}

fn collect_named_files(dir: &Path, target_name: &str, max_depth: usize, out: &mut Vec<PathBuf>) {
    if max_depth == 0 {
        return;
    }
    let Ok(entries) = fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            collect_named_files(&path, target_name, max_depth - 1, out);
        } else if path
            .file_name()
            .and_then(|name| name.to_str())
            .map(|name| name == target_name)
            .unwrap_or(false)
        {
            out.push(path);
        }
    }
}

fn choose_preferred_report(candidates: &mut [PathBuf]) -> Option<PathBuf> {
    candidates.sort_by_key(|path| {
        let text = path.to_string_lossy();
        let workflow_penalty = if text.contains("/workflow/") { 1 } else { 0 };
        let depth = path.components().count();
        (workflow_penalty, depth)
    });
    candidates.first().cloned()
}

fn hydrate_task_artifacts(task: &mut TaskStub) {
    let root = Path::new(&task.output_root);
    if !root.exists() {
        return;
    }

    let mut watch_logs = Vec::new();
    collect_named_files(root, "watch_events.jsonl", 5, &mut watch_logs);
    if let Some(watch_path) = choose_preferred_report(&mut watch_logs) {
        task.watch_path = Some(watch_path.to_string_lossy().into_owned());
        if let Ok(contents) = fs::read_to_string(&watch_path) {
            let mut events = Vec::new();
            let mut control_active = None;
            for line in contents
                .lines()
                .rev()
                .take(24)
                .collect::<Vec<_>>()
                .into_iter()
                .rev()
            {
                if let Ok(value) = serde_json::from_str::<Value>(line) {
                    if let Some(active) = value
                        .get("metadata")
                        .and_then(|meta| meta.get("control_active"))
                        .and_then(Value::as_bool)
                    {
                        control_active = Some(active);
                    }
                    events.push(WatchEventStub {
                        level: value
                            .get("level")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_string(),
                        message: value
                            .get("message")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_string(),
                        phase: value
                            .get("phase")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_string(),
                        detail: value
                            .get("detail")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_string(),
                        observation: value
                            .get("observation")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_string(),
                        reasoning: value
                            .get("reasoning")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_string(),
                        decision: value
                            .get("decision")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_string(),
                        action_name: value
                            .get("action_name")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_string(),
                    });
                }
            }
            task.watch_events = events;
            task.control_active = control_active.or_else(|| {
                (task.kind == "wechat_chat_summary" && task.status == "running").then_some(true)
            });
        }
    } else {
        task.watch_path = None;
        task.watch_events.clear();
        task.control_active = None;
    }

    let mut report_htmls = Vec::new();
    collect_named_files(root, "report.html", 5, &mut report_htmls);
    if let Some(report_path) = choose_preferred_report(&mut report_htmls) {
        task.result_kind = Some("html_report".to_string());
        task.result_path = Some(report_path.to_string_lossy().into_owned());
    } else {
        task.result_kind = None;
        task.result_path = None;
    }

    let mut report_jsons = Vec::new();
    collect_named_files(root, "report.json", 5, &mut report_jsons);
    if let Some(report_json_path) = choose_preferred_report(&mut report_jsons) {
        if let Ok(contents) = fs::read_to_string(report_json_path) {
            if let Ok(value) = serde_json::from_str::<Value>(&contents) {
                if let Some(assessment) = value.get("assessment") {
                    task.assessment_complete = assessment.get("complete").and_then(Value::as_bool);
                    task.assessment_confidence =
                        assessment.get("confidence").and_then(Value::as_f64);
                }
            }
        }
    }
}

fn spawn_clawvision(
    app: &AppHandle,
    id_prefix: &str,
    kind: &str,
    prompt: &str,
    output_segment: &str,
    args: &[OsString],
    output_flag: &str,
    model_mode: Option<String>,
    model_label: Option<String>,
) -> Result<TaskStub, String> {
    let runtime = resolve_runtime(app)?;
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|err| err.to_string())?;
    let millis = now.as_millis();
    let id = format!("{id_prefix}-{millis}");
    let output_root = runtime.output_root.join(output_segment).join(&id);
    fs::create_dir_all(&output_root)
        .map_err(|err| format!("Failed to create output dir: {err}"))?;
    let log_path = output_root.join("desktop.log");
    let stdout =
        File::create(&log_path).map_err(|err| format!("Failed to create log file: {err}"))?;
    let stderr = stdout
        .try_clone()
        .map_err(|err| format!("Failed to clone log file handle: {err}"))?;

    let mut child = Command::new(&runtime.launcher);
    child
        .current_dir(&runtime.workdir)
        .env("PYTHONUNBUFFERED", "1")
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr));

    match runtime.launcher_kind {
        LauncherKind::Binary => {}
        LauncherKind::Python => {
            child
                .env("PYTHONPATH", build_pythonpath(&runtime.workdir)?)
                .arg("-m")
                .arg("clawvision");
        }
    }

    for arg in args {
        child.arg(arg);
    }
    child.arg(output_flag).arg(&output_root);

    let child = child.spawn().map_err(|err| {
        format!(
            "Failed to start ClawVision runtime with {:?}: {err}",
            runtime.launcher
        )
    })?;

    Ok(make_task_stub(
        id,
        kind,
        prompt,
        millis.to_string(),
        log_path,
        output_root,
        child.id(),
        model_mode,
        model_label,
    ))
}

fn companion_is_alive(handle: &mut ChatbotsCompanionHandle) -> bool {
    matches!(handle.child.try_wait(), Ok(None))
}

fn stop_chatbots_companion(app: &AppHandle) {
    let state = app.state::<ChatbotsCompanionState>();
    if let Ok(mut guard) = state.0.lock() {
        if let Some(mut handle) = guard.take() {
            let _ = handle.child.kill();
            let _ = handle.child.wait();
        }
    };
}

fn parse_task_stub(value: &Value) -> Result<TaskStub, String> {
    serde_json::from_value::<TaskStub>(value.clone())
        .map_err(|err| format!("Failed to parse companion task payload: {err}"))
}

fn spawn_chatbots_companion(app: &AppHandle) -> Result<ChatbotsCompanionHandle, String> {
    let runtime = resolve_runtime(app)?;
    let companion_dir = app
        .path()
        .app_data_dir()
        .map_err(|err| format!("Failed to resolve app data dir: {err}"))?
        .join("companion");
    fs::create_dir_all(&companion_dir)
        .map_err(|err| format!("Failed to create companion dir: {err}"))?;
    let log_path = companion_dir.join("chatbots-companion.log");
    let stderr =
        File::create(&log_path).map_err(|err| format!("Failed to create companion log: {err}"))?;

    let mut child = Command::new(&runtime.launcher);
    child
        .current_dir(&runtime.workdir)
        .env("PYTHONUNBUFFERED", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::from(stderr));

    match runtime.launcher_kind {
        LauncherKind::Binary => {}
        LauncherKind::Python => {
            child
                .env("PYTHONPATH", build_pythonpath(&runtime.workdir)?)
                .arg("-m")
                .arg("clawvision");
        }
    }

    child
        .arg("chatbots-companion")
        .arg("--port")
        .arg("8765")
        .arg("--vision")
        .arg("qwen-local")
        .arg("--output-root-base")
        .arg(runtime.output_root.join("multi_chat"));

    let mut child = child
        .spawn()
        .map_err(|err| format!("Failed to start chatbot companion: {err}"))?;
    let stdin = child
        .stdin
        .take()
        .ok_or_else(|| "Companion stdin not available".to_string())?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "Companion stdout not available".to_string())?;
    let mut stdout = BufReader::new(stdout);
    let mut skipped_lines: Vec<String> = Vec::new();
    let mut saw_ready = false;
    for _ in 0..32 {
        let mut ready_line = String::new();
        let read = stdout
            .read_line(&mut ready_line)
            .map_err(|err| format!("Failed to read companion ready line: {err}"))?;
        if read == 0 {
            break;
        }
        let trimmed = ready_line.trim();
        if trimmed.is_empty() {
            continue;
        }
        match serde_json::from_str::<Value>(trimmed) {
            Ok(value) if value.get("type").and_then(Value::as_str) == Some("ready") => {
                saw_ready = true;
                break;
            }
            Ok(_) => {
                skipped_lines.push(trimmed.to_string());
            }
            Err(_) => {
                skipped_lines.push(trimmed.to_string());
            }
        }
    }
    if !saw_ready {
        let context = if skipped_lines.is_empty() {
            "none".to_string()
        } else {
            skipped_lines.join(" | ")
        };
        return Err(format!(
            "Chatbot companion exited before sending ready line. Preceding output: {context}"
        ));
    }

    Ok(ChatbotsCompanionHandle {
        child,
        stdin,
        stdout,
    })
}

fn with_chatbots_companion<T>(
    app: &AppHandle,
    mut f: impl FnMut(&mut ChatbotsCompanionHandle) -> Result<T, String>,
) -> Result<T, String> {
    let state = app.state::<ChatbotsCompanionState>();
    let mut guard = state
        .0
        .lock()
        .map_err(|_| "Chatbots companion state is poisoned".to_string())?;

    let needs_spawn = match guard.as_mut() {
        Some(handle) => !companion_is_alive(handle),
        None => true,
    };
    if needs_spawn {
        *guard = Some(spawn_chatbots_companion(app)?);
    }

    f(guard
        .as_mut()
        .ok_or_else(|| "Chatbots companion unavailable".to_string())?)
}

fn request_chatbots_companion(app: &AppHandle, request: Value) -> Result<Value, String> {
    with_chatbots_companion(app, |handle| {
        let line = serde_json::to_string(&request)
            .map_err(|err| format!("Failed to encode companion request: {err}"))?;
        handle
            .stdin
            .write_all(line.as_bytes())
            .and_then(|_| handle.stdin.write_all(b"\n"))
            .and_then(|_| handle.stdin.flush())
            .map_err(|err| format!("Failed to write companion request: {err}"))?;

        let mut response_line = String::new();
        handle
            .stdout
            .read_line(&mut response_line)
            .map_err(|err| format!("Failed to read companion response: {err}"))?;
        if response_line.trim().is_empty() {
            return Err("Companion returned an empty response".to_string());
        }
        serde_json::from_str::<Value>(response_line.trim())
            .map_err(|err| format!("Failed to decode companion response: {err}"))
    })
}

fn store_chatbots_task(app: &AppHandle, task: &TaskStub) {
    if let Ok(mut slot) = app.state::<LatestChatbotsTask>().0.lock() {
        *slot = Some(task.clone());
    }
    let _ = app.emit("chatbots-launch-requested", task);
}

fn spawn_chatbots_task(
    app: &AppHandle,
    question: &str,
    launch_source: &str,
) -> Result<TaskStub, String> {
    let trimmed = question.trim();
    if trimmed.is_empty() {
        return Err("Question is empty".to_string());
    }

    let response = request_chatbots_companion(
        app,
        serde_json::json!({
            "action": "ask_chatbots",
            "question": trimmed,
            "closeWindowsOnFinish": false,
            "launchSource": launch_source,
        }),
    )?;
    if response.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err(response
            .get("error")
            .and_then(Value::as_str)
            .unwrap_or("Chatbots companion request failed")
            .to_string());
    }
    let task = parse_task_stub(
        response
            .get("task")
            .ok_or_else(|| "Chatbots companion response missing task".to_string())?,
    )?;

    store_chatbots_task(app, &task);

    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }

    let _ = app.emit(
        "chatbots-launch-source",
        serde_json::json!({
            "source": launch_source,
            "taskId": task.id,
        }),
    );

    Ok(task)
}

fn extract_question_from_deep_link(url: &Url) -> Option<String> {
    let host = url.host_str().unwrap_or_default();
    let path = url.path().trim_matches('/');
    let target = host.eq_ignore_ascii_case("ask")
        || host.eq_ignore_ascii_case("chatbots")
        || path.eq_ignore_ascii_case("ask")
        || path.eq_ignore_ascii_case("chatbots");
    if !target {
        return None;
    }

    let question = url
        .query_pairs()
        .find_map(|(key, value)| (key == "question").then(|| value.into_owned()))?;
    let trimmed = question.trim();
    if trimmed.is_empty() {
        return None;
    }
    Some(trimmed.to_string())
}

fn handle_deep_links(app: &AppHandle, urls: &[Url]) {
    for url in urls {
        if let Some(question) = extract_question_from_deep_link(url) {
            let _ = spawn_chatbots_task(app, &question, "deep_link");
        }
    }
}

#[tauri::command]
fn latest_chatbots_task(
    app: AppHandle,
    state: State<'_, LatestChatbotsTask>,
) -> Result<Option<TaskStub>, String> {
    if let Ok(response) =
        request_chatbots_companion(&app, serde_json::json!({ "action": "latest_task" }))
    {
        if response.get("ok").and_then(Value::as_bool) == Some(true) {
            let task = match response.get("task") {
                Some(value) if !value.is_null() => Some(parse_task_stub(value)?),
                _ => None,
            };
            if let Ok(mut slot) = state.0.lock() {
                *slot = task.clone();
            }
            return Ok(task);
        }
    }

    let guard = state
        .0
        .lock()
        .map_err(|_| "Latest chatbots task state is poisoned".to_string())?;
    Ok(guard.clone())
}

fn is_pid_alive(pid: u32) -> bool {
    // kill(pid, 0) checks if process exists without sending a signal
    unsafe { libc::kill(pid as i32, 0) == 0 }
}

fn reap_pid_if_exited(pid: u32) -> bool {
    let mut status: libc::c_int = 0;
    let result = unsafe { libc::waitpid(pid as i32, &mut status, libc::WNOHANG) };
    result == pid as i32
}

fn task_status_from_log(log_path: &str) -> Option<String> {
    let contents = fs::read_to_string(log_path).ok()?;
    if contents.contains("TASK COMPLETE") {
        return Some("done".to_string());
    }
    if contents.contains("TASK FAILED") {
        return Some("failed".to_string());
    }
    None
}

#[tauri::command]
fn check_task_status(state: State<'_, RunningTasks>) -> Vec<TaskStub> {
    let mut guard = match state.0.lock() {
        Ok(g) => g,
        Err(_) => return vec![],
    };
    for task in guard.iter_mut() {
        if task.status == "running" {
            if let Some(status) = task_status_from_log(&task.log_path) {
                task.status = status;
            } else if reap_pid_if_exited(task.pid) || !is_pid_alive(task.pid) {
                task.status = "done".to_string();
            }
        }
        hydrate_task_artifacts(task);
    }
    guard.clone()
}

#[tauri::command]
fn reveal_path(path: String) -> Result<(), String> {
    let target = PathBuf::from(&path);
    if !target.exists() {
        return Err(format!("Path does not exist: {path}"));
    }

    let mut command = Command::new("open");
    if target.is_file() {
        command.arg("-R").arg(&target);
    } else {
        command.arg(&target);
    }
    command
        .spawn()
        .map_err(|err| format!("Failed to reveal path {path}: {err}"))?;
    Ok(())
}

#[tauri::command]
fn stop_task(task_id: String, state: State<'_, RunningTasks>) -> Result<String, String> {
    let mut guard = state
        .0
        .lock()
        .map_err(|_| "Task state is poisoned".to_string())?;
    let task = guard
        .iter_mut()
        .find(|t| t.id == task_id)
        .ok_or_else(|| format!("Task {task_id} not found"))?;
    if task.status != "running" {
        return Err(format!("Task {} is already {}", task_id, task.status));
    }
    let pid = task.pid as i32;
    // Send SIGTERM first for graceful shutdown
    unsafe {
        libc::kill(pid, libc::SIGTERM);
    }
    // Give it a moment, then force kill
    std::thread::sleep(std::time::Duration::from_millis(500));
    if is_pid_alive(task.pid) {
        unsafe {
            libc::kill(pid, libc::SIGKILL);
        }
    }
    task.status = "stopped".to_string();
    Ok(format!("Task {task_id} stopped"))
}

fn extract_profile_url(prompt: &str) -> Option<String> {
    prompt
        .split_whitespace()
        .map(|part| part.trim_matches(|c: char| "\"'()[]{}<>，。,!?！？；;".contains(c)))
        .find(|part| part.contains("/user/profile/"))
        .map(ToOwned::to_owned)
}

fn infer_kind(prompt: &str) -> Result<&'static str, String> {
    let lower = prompt.to_lowercase();
    let looks_like_wechat = (prompt.contains("微信") || lower.contains("wechat"))
        && (prompt.contains("会话")
            || prompt.contains("聊天")
            || prompt.contains("聊天记录")
            || prompt.contains("对话"))
        && (prompt.contains("总结") || prompt.contains("摘要") || prompt.contains("梳理"));
    if looks_like_wechat {
        return Ok("wechat_chat_summary");
    }

    if extract_profile_url(prompt).is_some() {
        return Ok("creator_growth_breakdown");
    }

    let creator_hints = ["作者", "博主", "起号", "账号", "profile", "creator"];
    if creator_hints.iter().any(|hint| prompt.contains(hint)) {
        return Err(
            "Creator tasks currently require a Xiaohongshu profile URL in the prompt.".to_string(),
        );
    }

    Ok("topic_research")
}

fn parse_xhs_model_mode(
    model_mode: &str,
) -> Result<(&'static str, &'static str, &'static str), String> {
    match model_mode.trim() {
        "" | "cloud" => Ok(("cloud", "Cloud Claude Sonnet", "sonnet")),
        "local9b" => Ok(("local9b", "Local Qwen 3.5 9B", "qwen-local")),
        other => Err(format!("Unsupported XHS model mode: {other}")),
    }
}

#[tauri::command]
fn start_task(
    app: AppHandle,
    prompt: String,
    model_mode: Option<String>,
) -> Result<TaskStub, String> {
    let trimmed = prompt.trim();
    if trimmed.is_empty() {
        return Err("Task prompt is empty".to_string());
    }

    let kind = infer_kind(trimmed)?.to_string();
    let requested_model_mode = model_mode.unwrap_or_else(|| "cloud".to_string());
    let (model_mode_value, model_label, llm_backend) = if kind == "wechat_chat_summary" {
        ("local_qwen_wechat", "Local Qwen 2B + 9B", "qwen-local")
    } else {
        parse_xhs_model_mode(&requested_model_mode)?
    };
    // Stop the chatbots companion if running — it holds the WebSocket port
    // the task runner needs for the Chrome extension bridge.
    stop_chatbots_companion(&app);

    let task = spawn_clawvision(
        &app,
        "task",
        &kind,
        trimmed,
        if kind == "wechat_chat_summary" {
            "desktop_app_wechat"
        } else {
            "desktop_app"
        },
        &[
            OsString::from("desktop"),
            OsString::from("run"),
            OsString::from("--prompt"),
            OsString::from(trimmed),
            OsString::from("--llm-backend"),
            OsString::from(llm_backend),
        ],
        "--output-root",
        Some(model_mode_value.to_string()),
        Some(model_label.to_string()),
    )?;

    if let Ok(mut guard) = app.state::<RunningTasks>().0.lock() {
        guard.push(task.clone());
        // Keep only the latest 10 tasks
        let excess = guard.len().saturating_sub(10);
        if excess > 0 {
            guard.drain(..excess);
        }
    }

    Ok(task)
}

#[tauri::command]
fn ask_chatbots(app: AppHandle, question: String) -> Result<TaskStub, String> {
    spawn_chatbots_task(&app, &question, "desktop_ui")
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let builder = tauri::Builder::default()
        .manage(LatestChatbotsTask::default())
        .manage(RunningTasks::default())
        .manage(ChatbotsCompanionState::default())
        .plugin(tauri_plugin_deep_link::init())
        .setup(|app| {
            let app_handle = app.handle().clone();

            if let Some(urls) = app.deep_link().get_current()? {
                handle_deep_links(&app_handle, &urls);
            }

            let app_handle_for_events = app_handle.clone();
            app.deep_link().on_open_url(move |event| {
                let urls = event.urls();
                handle_deep_links(&app_handle_for_events, &urls);
            });

            Ok(())
        });

    builder
        .invoke_handler(tauri::generate_handler![
            app_health,
            start_task,
            check_task_status,
            stop_task,
            ask_chatbots,
            latest_chatbots_task,
            reveal_path
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    use super::{
        extract_profile_url, extract_question_from_deep_link, infer_kind, parse_xhs_model_mode,
        task_status_from_log,
    };
    use std::fs;
    use url::Url;

    #[test]
    fn extracts_profile_url_from_prompt() {
        let prompt = "拆解作者 https://www.xiaohongshu.com/user/profile/665e81660000000003033638";
        let url = extract_profile_url(prompt).expect("profile url");
        assert!(url.ends_with("665e81660000000003033638"));
    }

    #[test]
    fn infers_topic_task_without_creator_url() {
        assert_eq!(infer_kind("研究护肤干货").unwrap(), "topic_research");
    }

    #[test]
    fn infers_wechat_summary_task() {
        assert_eq!(
            infer_kind("请总结微信会话“冬虫夏草”的聊天记录").unwrap(),
            "wechat_chat_summary"
        );
    }

    #[test]
    fn extracts_question_from_chatbots_deep_link() {
        let url = Url::parse("clawvision://ask?question=Reply%20READY").expect("url");
        assert_eq!(
            extract_question_from_deep_link(&url).as_deref(),
            Some("Reply READY")
        );
    }

    #[test]
    fn ignores_unrelated_deep_link() {
        let url = Url::parse("clawvision://settings?question=ignored").expect("url");
        assert!(extract_question_from_deep_link(&url).is_none());
    }

    #[test]
    fn detects_completed_task_from_log_marker() {
        let path = std::env::temp_dir().join("clawvision-task-status-test.log");
        fs::write(&path, "hello\nTASK COMPLETE — 10.1s\n").expect("write temp log");
        assert_eq!(
            task_status_from_log(path.to_string_lossy().as_ref()).as_deref(),
            Some("done")
        );
        let _ = fs::remove_file(path);
    }

    #[test]
    fn parses_xhs_model_modes() {
        assert_eq!(parse_xhs_model_mode("cloud").unwrap().2, "sonnet");
        assert_eq!(parse_xhs_model_mode("local9b").unwrap().2, "qwen-local");
        assert!(parse_xhs_model_mode("unknown").is_err());
    }
}
