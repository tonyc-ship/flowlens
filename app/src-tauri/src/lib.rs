use base64::prelude::*;
use serde::Serialize;
use serde_json::Value;
use std::env;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use tauri::Manager;

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

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ScreenshotArtifact {
    label: String,
    path: String,
    data_url: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct PrototypeCommandResult {
    action: String,
    ok: bool,
    exit_code: Option<i32>,
    stdout: String,
    stderr: String,
    json: Option<Value>,
    screenshots: Vec<ScreenshotArtifact>,
}

#[tauri::command]
fn app_health() -> HealthStatus {
    HealthStatus {
        app_name: "Socai Prototype",
        version: env!("CARGO_PKG_VERSION"),
        os: std::env::consts::OS,
        arch: std::env::consts::ARCH,
        backend_mode: "Tauri + Python CDP prototype scripts",
        ready: true,
    }
}

#[tauri::command]
fn connect_chrome(app: tauri::AppHandle) -> Result<PrototypeCommandResult, String> {
    run_prototype_action(&app, "connect_chrome")
}

#[tauri::command]
fn list_chrome_targets(app: tauri::AppHandle) -> Result<PrototypeCommandResult, String> {
    run_prototype_action(&app, "list_targets")
}

#[tauri::command]
fn create_controlled_tab(app: tauri::AppHandle) -> Result<PrototypeCommandResult, String> {
    run_prototype_action(&app, "controlled_tab")
}

#[tauri::command]
fn open_xhs_probe(app: tauri::AppHandle) -> Result<PrototypeCommandResult, String> {
    run_prototype_action(&app, "xhs_probe")
}

#[tauri::command]
fn capture_test_screenshot(app: tauri::AppHandle) -> Result<PrototypeCommandResult, String> {
    run_prototype_action(&app, "capture_test_screenshot")
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

fn run_prototype_action(
    app: &tauri::AppHandle,
    action: &str,
) -> Result<PrototypeCommandResult, String> {
    let repo_root = repo_root()?;
    let (script, extra_args): (&str, &[&str]) = match action {
        "connect_chrome" => ("chrome_discovery.py", &["--json"]),
        "list_targets" => ("cdp_targets.py", &["--json", "--timeout", "30"]),
        "controlled_tab" => ("cdp_controlled_tab.py", &["--json", "--timeout", "30"]),
        "capture_test_screenshot" => ("cdp_controlled_tab.py", &["--json", "--timeout", "30"]),
        "xhs_probe" => ("cdp_xhs_probe.py", &["--json", "--timeout", "30"]),
        _ => return Err(format!("Unknown Socai prototype action: {action}")),
    };

    let script_path = repo_root.join("app").join("prototype").join(script);
    if !script_path.exists() {
        return Err(format!(
            "Prototype script not found: {}",
            script_path.display()
        ));
    }

    let output = if script == "chrome_discovery.py" {
        let python = env::var("SOCAI_PYTHON").unwrap_or_else(|_| "python3".to_string());
        Command::new(python)
            .arg(&script_path)
            .args(extra_args)
            .current_dir(&repo_root)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .output()
            .map_err(|err| format!("Failed to run {script}: {err}"))?
    } else {
        Command::new("uv")
            .arg("run")
            .arg("--no-project")
            .arg("--with")
            .arg("cdp-use==1.4.5")
            .arg("--python")
            .arg("3.11")
            .arg("python")
            .arg(&script_path)
            .args(extra_args)
            .current_dir(&repo_root)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .output()
            .map_err(|err| format!("Failed to run {script} via uv: {err}"))?
    };

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    let json = parse_json_stdout(&stdout);
    let screenshots = json
        .as_ref()
        .map(collect_screenshot_artifacts)
        .transpose()?
        .unwrap_or_default();

    // Touch app handle so command is explicitly app-scoped and ready for later app-data paths.
    let _ = app.path().app_data_dir();

    Ok(PrototypeCommandResult {
        action: action.to_string(),
        ok: output.status.success(),
        exit_code: output.status.code(),
        stdout,
        stderr,
        json,
        screenshots,
    })
}

fn repo_root() -> Result<PathBuf, String> {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .map_err(|err| format!("Failed to resolve repo root from Socai Tauri app: {err}"))
}

fn parse_json_stdout(stdout: &str) -> Option<Value> {
    serde_json::from_str(stdout).ok()
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
        .invoke_handler(tauri::generate_handler![
            app_health,
            connect_chrome,
            list_chrome_targets,
            create_controlled_tab,
            open_xhs_probe,
            capture_test_screenshot,
            open_chrome_inspect
        ])
        .run(tauri::generate_context!())
        .expect("error while running Socai prototype");
}
