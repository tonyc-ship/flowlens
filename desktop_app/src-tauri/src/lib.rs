use serde::Serialize;
use std::env;
use std::fs::{self, File};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{SystemTime, UNIX_EPOCH};

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
struct TaskStub {
    id: String,
    kind: String,
    prompt: String,
    status: &'static str,
    created_at: String,
    log_path: String,
    output_root: String,
    pid: u32,
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

fn extract_profile_url(prompt: &str) -> Option<String> {
    prompt
        .split_whitespace()
        .map(|part| part.trim_matches(|c: char| "\"'()[]{}<>，。,!?！？；;".contains(c)))
        .find(|part| part.contains("/user/profile/"))
        .map(ToOwned::to_owned)
}

fn infer_kind(prompt: &str) -> Result<&'static str, String> {
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

#[tauri::command]
fn start_task(prompt: String) -> Result<TaskStub, String> {
    let trimmed = prompt.trim();
    if trimmed.is_empty() {
        return Err("Task prompt is empty".to_string());
    }

    let kind = infer_kind(trimmed)?.to_string();
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|err| err.to_string())?;
    let millis = now.as_millis();
    let id = format!("task-{}", millis);
    let repo_root = repo_root()?;
    let output_root = repo_root.join("task_runs").join("desktop_app").join(&id);
    fs::create_dir_all(&output_root).map_err(|err| format!("Failed to create output dir: {err}"))?;
    let log_path = output_root.join("desktop.log");
    let stdout = File::create(&log_path).map_err(|err| format!("Failed to create log file: {err}"))?;
    let stderr = stdout
        .try_clone()
        .map_err(|err| format!("Failed to clone log file handle: {err}"))?;

    let python = env::var("CLAWVISION_PYTHON").unwrap_or_else(|_| "python".to_string());
    let child = Command::new(&python)
        .current_dir(&repo_root)
        .env("PYTHONUNBUFFERED", "1")
        .arg("-m")
        .arg("clawvision")
        .arg("desktop")
        .arg("run")
        .arg("--prompt")
        .arg(trimmed)
        .arg("--output-root")
        .arg(&output_root)
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr))
        .spawn()
        .map_err(|err| format!("Failed to start Python task with {python}: {err}"))?;

    Ok(TaskStub {
        id,
        kind,
        prompt: trimmed.to_string(),
        status: "running",
        created_at: millis.to_string(),
        log_path: log_path.to_string_lossy().into_owned(),
        output_root: output_root.to_string_lossy().into_owned(),
        pid: child.id(),
    })
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![app_health, start_task])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    use super::{extract_profile_url, infer_kind};

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
}
