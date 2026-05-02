use base64::prelude::*;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::env;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use tauri::{Manager, State};

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct HealthStatus {
    app_name: String,
    version: String,
    os: String,
    arch: String,
    backend_mode: String,
    ready: bool,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ScreenshotArtifact {
    label: String,
    path: String,
    data_url: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct RuntimeCommandResult {
    action: String,
    ok: bool,
    exit_code: Option<i32>,
    stdout: String,
    stderr: String,
    json: Option<Value>,
    screenshots: Vec<ScreenshotArtifact>,
}

#[derive(Default)]
struct RuntimeState {
    client: Mutex<Option<RuntimeClient>>,
}

struct RuntimeClient {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<std::process::ChildStdout>,
    next_id: u64,
}

struct SidecarLaunch {
    program: PathBuf,
    args: Vec<String>,
    current_dir: PathBuf,
    envs: Vec<(String, String)>,
}

#[tauri::command]
fn app_health(
    app: tauri::AppHandle,
    state: State<'_, RuntimeState>,
) -> Result<HealthStatus, String> {
    let value = send_runtime_request(&app, &state, "health", json!({}))?;
    serde_json::from_value(value)
        .map_err(|err| format!("Runtime health response was invalid: {err}"))
}

#[tauri::command]
fn connect_chrome(
    app: tauri::AppHandle,
    state: State<'_, RuntimeState>,
) -> Result<RuntimeCommandResult, String> {
    runtime_action(&app, &state, "connect_chrome")
}

#[tauri::command]
fn list_chrome_targets(
    app: tauri::AppHandle,
    state: State<'_, RuntimeState>,
) -> Result<RuntimeCommandResult, String> {
    runtime_action(&app, &state, "list_chrome_targets")
}

#[tauri::command]
fn create_controlled_tab(
    app: tauri::AppHandle,
    state: State<'_, RuntimeState>,
) -> Result<RuntimeCommandResult, String> {
    runtime_action(&app, &state, "create_controlled_tab")
}

#[tauri::command]
fn open_xhs_probe(
    app: tauri::AppHandle,
    state: State<'_, RuntimeState>,
) -> Result<RuntimeCommandResult, String> {
    runtime_action(&app, &state, "open_xhs_probe")
}

#[tauri::command]
fn xhs_connection_test(
    app: tauri::AppHandle,
    state: State<'_, RuntimeState>,
) -> Result<RuntimeCommandResult, String> {
    runtime_action(&app, &state, "xhs_connection_test")
}

#[tauri::command]
fn capture_test_screenshot(
    app: tauri::AppHandle,
    state: State<'_, RuntimeState>,
) -> Result<RuntimeCommandResult, String> {
    runtime_action(&app, &state, "capture_test_screenshot")
}

#[tauri::command]
fn open_chrome_inspect() -> Result<(), String> {
    let inspect_url = "chrome://inspect/#remote-debugging";
    let status = if cfg!(target_os = "macos") {
        Command::new("open")
            .arg("-a")
            .arg("Google Chrome")
            .arg(inspect_url)
            .status()
    } else if cfg!(target_os = "windows") {
        Command::new("cmd")
            .arg("/C")
            .arg("start")
            .arg(inspect_url)
            .status()
    } else {
        Command::new("xdg-open").arg(inspect_url).status()
    }
    .map_err(|err| format!("Failed to open Chrome inspect page: {err}"))?;

    if status.success() {
        Ok(())
    } else {
        Err(format!(
            "Opening Chrome inspect page exited with status: {status}"
        ))
    }
}

fn runtime_action(
    app: &tauri::AppHandle,
    state: &State<'_, RuntimeState>,
    method: &str,
) -> Result<RuntimeCommandResult, String> {
    let value = send_runtime_request(app, state, method, json!({}))?;
    runtime_command_result(value)
}

fn send_runtime_request(
    app: &tauri::AppHandle,
    state: &State<'_, RuntimeState>,
    method: &str,
    params: Value,
) -> Result<Value, String> {
    let mut guard = state
        .client
        .lock()
        .map_err(|_| "Socai runtime state lock was poisoned".to_string())?;

    if guard.is_none() {
        *guard = Some(RuntimeClient::start(app)?);
    }

    let client = guard
        .as_mut()
        .expect("runtime client should be initialized");
    match client.send_request(method, params) {
        Ok(value) => Ok(value),
        Err(err) => {
            client.kill();
            *guard = None;
            Err(err)
        }
    }
}

fn runtime_command_result(value: Value) -> Result<RuntimeCommandResult, String> {
    let json_value = value.get("json").cloned().filter(|value| !value.is_null());
    let screenshots = json_value
        .as_ref()
        .map(collect_screenshot_artifacts)
        .transpose()?
        .unwrap_or_default();

    Ok(RuntimeCommandResult {
        action: string_field(&value, "action").unwrap_or_else(|| "runtime_action".to_string()),
        ok: value.get("ok").and_then(Value::as_bool).unwrap_or(false),
        exit_code: value
            .get("exitCode")
            .and_then(Value::as_i64)
            .and_then(|code| i32::try_from(code).ok()),
        stdout: string_field(&value, "stdout").unwrap_or_default(),
        stderr: string_field(&value, "stderr").unwrap_or_default(),
        json: json_value,
        screenshots,
    })
}

fn string_field(value: &Value, field: &str) -> Option<String> {
    value
        .get(field)
        .and_then(Value::as_str)
        .map(ToString::to_string)
}

impl RuntimeClient {
    fn start(app: &tauri::AppHandle) -> Result<Self, String> {
        let launch = resolve_sidecar_launch(app)?;
        let mut command = Command::new(&launch.program);
        command
            .args(&launch.args)
            .current_dir(&launch.current_dir)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .env("PYTHONUNBUFFERED", "1");

        for (key, value) in launch.envs {
            command.env(key, value);
        }

        let mut child = command.spawn().map_err(|err| {
            format!(
                "Failed to start Socai Python runtime sidecar ({}): {err}",
                launch.program.display()
            )
        })?;

        if let Some(stderr) = child.stderr.take() {
            thread::spawn(move || {
                let reader = BufReader::new(stderr);
                for line in reader.lines().map_while(Result::ok) {
                    eprintln!("[socai-runtime] {line}");
                }
            });
        }

        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| "Failed to open Socai runtime stdin".to_string())?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| "Failed to open Socai runtime stdout".to_string())?;

        Ok(Self {
            child,
            stdin,
            stdout: BufReader::new(stdout),
            next_id: 1,
        })
    }

    fn send_request(&mut self, method: &str, params: Value) -> Result<Value, String> {
        let id = self.next_id;
        self.next_id += 1;

        let request = json!({
            "jsonrpc": "2.0",
            "id": id,
            "method": method,
            "params": params,
        });
        writeln!(self.stdin, "{request}")
            .and_then(|_| self.stdin.flush())
            .map_err(|err| format!("Failed to write to Socai runtime: {err}"))?;

        let mut line = String::new();
        loop {
            line.clear();
            let bytes = self
                .stdout
                .read_line(&mut line)
                .map_err(|err| format!("Failed to read from Socai runtime: {err}"))?;
            if bytes == 0 {
                return Err("Socai runtime exited before responding".to_string());
            }

            let message: Value = serde_json::from_str(line.trim()).map_err(|err| {
                format!("Socai runtime returned invalid JSON-RPC: {err}; line={line:?}")
            })?;

            if message.get("id").and_then(Value::as_u64) != Some(id) {
                if let Some(method) = message.get("method").and_then(Value::as_str) {
                    eprintln!("[socai-runtime-event] {method}: {message}");
                }
                continue;
            }

            if let Some(error) = message.get("error") {
                let message = error
                    .get("message")
                    .and_then(Value::as_str)
                    .unwrap_or("Socai runtime request failed");
                return Err(message.to_string());
            }

            return Ok(message.get("result").cloned().unwrap_or(Value::Null));
        }
    }

    fn kill(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn resolve_sidecar_launch(app: &tauri::AppHandle) -> Result<SidecarLaunch, String> {
    if let Ok(python) = env::var("SOCAI_DESKTOP_RUNTIME_PYTHON") {
        return source_sidecar_launch(PathBuf::from(python));
    }

    if let Ok(resource_dir) = app.path().resource_dir() {
        let bundled_python = resource_dir
            .join("socai-runtime")
            .join("bin")
            .join("python3");
        if bundled_python.exists() {
            let current_dir = app
                .path()
                .app_data_dir()
                .unwrap_or_else(|_| env::temp_dir().join("socai"));
            let _ = std::fs::create_dir_all(&current_dir);
            return Ok(SidecarLaunch {
                program: bundled_python,
                args: vec![
                    "-m".to_string(),
                    "socai.runtime".to_string(),
                    "--transport".to_string(),
                    "stdio".to_string(),
                ],
                current_dir,
                envs: vec![("SOCAI_DESKTOP_BUNDLED_RUNTIME".to_string(), "1".to_string())],
            });
        }
    }

    let python = match env::var("SOCAI_PYTHON") {
        Ok(path) => PathBuf::from(path),
        Err(_) => {
            let root = repo_root()?;
            let venv_python = root.join(".venv").join("bin").join("python");
            if venv_python.exists() {
                venv_python
            } else {
                PathBuf::from("python3")
            }
        }
    };
    source_sidecar_launch(python)
}

fn source_sidecar_launch(python: PathBuf) -> Result<SidecarLaunch, String> {
    let root = repo_root()?;
    let python_path = match env::var("PYTHONPATH") {
        Ok(existing) if !existing.is_empty() => format!("{}:{existing}", root.display()),
        _ => root.display().to_string(),
    };

    Ok(SidecarLaunch {
        program: python,
        args: vec![
            "-m".to_string(),
            "socai.runtime".to_string(),
            "--transport".to_string(),
            "stdio".to_string(),
        ],
        current_dir: root.clone(),
        envs: vec![
            ("PYTHONPATH".to_string(), python_path),
            ("SOCAI_REPO_ROOT".to_string(), root.display().to_string()),
        ],
    })
}

fn repo_root() -> Result<PathBuf, String> {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .map_err(|err| format!("Failed to resolve Socai repository root: {err}"))
}

fn collect_screenshot_artifacts(value: &Value) -> Result<Vec<ScreenshotArtifact>, String> {
    let mut artifacts = Vec::new();

    if let Some(path) = value.get("screenshot_path").and_then(Value::as_str) {
        artifacts.push(read_screenshot_artifact("screenshot", path)?);
    }

    if let Some(screenshots) = value.get("screenshots").and_then(Value::as_object) {
        for (label, path_value) in screenshots {
            if let Some(path) = path_value.as_str() {
                artifacts.push(read_screenshot_artifact(label, path)?);
            }
        }
    }

    Ok(artifacts)
}

fn read_screenshot_artifact(label: &str, path: &str) -> Result<ScreenshotArtifact, String> {
    let bytes =
        std::fs::read(path).map_err(|err| format!("Failed to read screenshot {path}: {err}"))?;
    Ok(ScreenshotArtifact {
        label: label.to_string(),
        path: path.to_string(),
        data_url: format!("data:image/png;base64,{}", BASE64_STANDARD.encode(bytes)),
    })
}

pub fn run() {
    tauri::Builder::default()
        .manage(RuntimeState::default())
        .invoke_handler(tauri::generate_handler![
            app_health,
            connect_chrome,
            list_chrome_targets,
            create_controlled_tab,
            open_xhs_probe,
            xhs_connection_test,
            capture_test_screenshot,
            open_chrome_inspect
        ])
        .run(tauri::generate_context!())
        .expect("error while running Socai");
}
