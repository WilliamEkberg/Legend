use std::sync::Mutex;

use tauri::Manager;
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

// The port the bundled backend sidecar listens on. It is deliberately fixed to
// 8000 so it always matches the frontend's production fallback in
// `src/api/client.ts` (`http://127.0.0.1:8000`). That makes the packaged app
// correct even if the `window.__LEGEND_API_BASE__` injection below loses the
// race against the frontend bundle's module-load-time `API_BASE` computation.
//
// FUTURE: to use a dynamically-chosen free port (bind 127.0.0.1:0, read it,
// drop the listener, pass `--port <p>`) without a startup race, inject the base
// URL via `WebviewWindowBuilder::initialization_script` (runs before any page
// script) rather than `WebviewWindow::eval` (races the page load). That requires
// building the window in Rust instead of from tauri.conf.json.
const BACKEND_PORT: u16 = 8000;

/// Holds the spawned backend child so we can kill it on app exit (no orphaned
/// uvicorn process — the packaged app must do in Rust what `start.sh` does with
/// a shell trap in dev).
struct SidecarState(Mutex<Option<CommandChild>>);

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  let app = tauri::Builder::default()
    .plugin(tauri_plugin_dialog::init())
    .plugin(tauri_plugin_fs::init())
    .plugin(tauri_plugin_shell::init())
    .manage(SidecarState(Mutex::new(None)))
    .setup(|app| {
      if cfg!(debug_assertions) {
        app.handle().plugin(
          tauri_plugin_log::Builder::default()
            .level(log::LevelFilter::Info)
            .build(),
        )?;
      }

      spawn_backend(app.handle());
      Ok(())
    })
    .build(tauri::generate_context!())
    .expect("error while building tauri application");

  app.run(|app_handle, event| {
    // Kill the backend sidecar when the app exits so no uvicorn is left running.
    if let tauri::RunEvent::Exit = event {
      if let Some(state) = app_handle.try_state::<SidecarState>() {
        if let Ok(mut guard) = state.0.lock() {
          if let Some(child) = guard.take() {
            let _ = child.kill();
          }
        }
      }
    }
  });
}

/// Spawn the bundled Python backend as a Tauri sidecar, expose its origin to the
/// webview, and health-check it in the background. Failures are logged, not
/// fatal: in `tauri dev` there may be no bundled binary and the frontend reaches
/// a manually-run backend through the Vite proxy instead.
fn spawn_backend(app: &tauri::AppHandle) {
  let sidecar = match app.shell().sidecar("legend-backend") {
    Ok(cmd) => cmd.args(["--port", &BACKEND_PORT.to_string()]),
    Err(e) => {
      log::warn!("[sidecar] could not create backend command: {e} (dev proxy fallback in use)");
      return;
    }
  };

  let (mut rx, child) = match sidecar.spawn() {
    Ok(pair) => pair,
    Err(e) => {
      log::warn!("[sidecar] could not spawn backend: {e} (dev proxy fallback in use)");
      return;
    }
  };

  // Store the child so the exit handler can kill it.
  if let Some(state) = app.try_state::<SidecarState>() {
    if let Ok(mut guard) = state.0.lock() {
      *guard = Some(child);
    }
  }

  // Tell the webview where the backend lives (highest-priority source read by
  // resolveApiBase() in src/api/client.ts). Belt-and-suspenders: the port equals
  // the frontend's prod fallback, so this is correct even if it lands late.
  if let Some(window) = app.get_webview_window("main") {
    let js = format!("window.__LEGEND_API_BASE__ = 'http://127.0.0.1:{BACKEND_PORT}';");
    let _ = window.eval(&js);
  }

  // Drain the sidecar's stdout/stderr into the log.
  tauri::async_runtime::spawn(async move {
    while let Some(event) = rx.recv().await {
      match event {
        CommandEvent::Stdout(bytes) | CommandEvent::Stderr(bytes) => {
          log::info!("[sidecar] {}", String::from_utf8_lossy(&bytes).trim_end());
        }
        CommandEvent::Error(err) => log::error!("[sidecar] {err}"),
        CommandEvent::Terminated(payload) => {
          log::warn!("[sidecar] backend terminated: {payload:?}");
        }
        _ => {}
      }
    }
  });

  // Poll the backend port until it accepts connections (~10s), then log readiness.
  std::thread::spawn(move || {
    for _ in 0..40 {
      if std::net::TcpStream::connect(("127.0.0.1", BACKEND_PORT)).is_ok() {
        log::info!("[sidecar] backend ready on 127.0.0.1:{BACKEND_PORT}");
        return;
      }
      std::thread::sleep(std::time::Duration::from_millis(250));
    }
    log::warn!("[sidecar] backend did not become ready on 127.0.0.1:{BACKEND_PORT}");
  });
}
