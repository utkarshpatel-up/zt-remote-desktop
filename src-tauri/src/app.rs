//! Command line, the headless server, and the desktop app (window + tray).

use crate::host::{self, auth, config, Host, Options, Session, UpdateInfo, VERSION};
use serde::Serialize;
use std::path::PathBuf;
use std::sync::Arc;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, State, WindowEvent};

pub struct Args {
    pub headless: bool,
    pub minimized: bool,
    pub av_server: Option<String>,
    pub opts: Options,
}

const HELP: &str = "ZT Remote Desktop

  --headless          run the server only, no window (prints to the console)
  --demo              test pattern + logged input, no real screen control
  --demo-still        with --demo: a still picture
  --port N            port (default from config, 8765)
  --bind auto|all|IP  auto = ZeroTier only (default)
  --av-server URL     AV Backup team server, e.g. http://10.7.0.5:8090 (saved)
  --config-dir DIR    where zt-remote-config.json lives
  --minimized         start hidden in the tray (used by start-at-login)
  --version";

pub fn parse_args(args: &[String]) -> Result<Args, String> {
    let mut a = Args {
        headless: false,
        minimized: false,
        av_server: None,
        opts: Options { demo: false, demo_still: false, bind: "auto".into(), port: None, config_dir: config::default_dir() },
    };
    let mut it = args.iter();
    while let Some(x) = it.next() {
        let mut val = || it.next().cloned().ok_or(format!("{x} needs a value\n\n{HELP}"));
        match x.as_str() {
            "--headless" => a.headless = true,
            "--minimized" => a.minimized = true,
            "--demo" => a.opts.demo = true,
            "--demo-still" => a.opts.demo_still = true,
            "--port" => a.opts.port = Some(val()?.parse().map_err(|_| "bad --port".to_string())?),
            "--bind" => a.opts.bind = val()?,
            "--av-server" => a.av_server = Some(val()?),
            "--config-dir" => a.opts.config_dir = PathBuf::from(val()?),
            "--version" => return Err(format!("ZT Remote Desktop {VERSION}")),
            "-h" | "--help" => return Err(HELP.to_string()),
            // macOS adds -psn_... when launched from Finder on old systems
            s if s.starts_with("-psn_") => {}
            other => return Err(format!("unknown option {other}\n\n{HELP}")),
        }
    }
    Ok(a)
}

fn apply_av_server(host: &Host, v: &Option<String>) {
    if let Some(s) = v {
        let url = auth::normalize_server_url(s);
        if !s.trim().is_empty() && url.is_empty() {
            eprintln!("Not a valid server address: {s}");
            std::process::exit(2);
        }
        let _ = host.update_cfg(|c| c.av_backup_server = url);
    }
}

fn banner(host: &Host) {
    let cfg = host.cfg.read().unwrap().clone();
    let (av, src) = host.av_server();
    println!("==========================================================");
    println!("  ZT Remote Desktop v{VERSION}{}", if host.opts.demo { "  (DEMO)" } else { "" });
    println!("  Access password: {}", cfg.token);
    println!("  (in {} - keep it private)", host.store.path.display());
    if av.is_empty() {
        println!("  AV Backup login: OFF (use --av-server http://<team-server>:8090)");
    } else {
        println!("  AV Backup login: ON ({av}, from {src}); roles: {}", cfg.av_backup_roles.join(", "));
    }
    println!("==========================================================");
}

pub fn run_headless(a: Args) {
    let host = Host::new(a.opts);
    apply_av_server(&host, &a.av_server);
    banner(&host);
    let rt = tokio::runtime::Runtime::new().expect("tokio runtime");
    rt.block_on(async {
        tokio::spawn(host::update::run(host.clone()));
        host::server::run(host).await;
    });
}

// ------------------------------------------------------------------ desktop

#[derive(Serialize)]
struct UiState {
    version: String,
    demo: bool,
    platform: &'static str,
    listening: Vec<String>,
    waiting_for_zerotier: bool,
    error: Option<String>,
    password: String,
    config_path: String,
    av_server: String,
    av_source: &'static str,
    av_server_setting: String,
    roles: Vec<String>,
    sessions: Vec<Session>,
    update: Option<UpdateInfo>,
    can_self_update: bool,
    log: Vec<String>,
    permissions: Permissions,
}

#[derive(Serialize)]
struct Permissions {
    /// None where the OS has no such switch
    screen: Option<bool>,
    accessibility: Option<bool>,
}

#[cfg(target_os = "macos")]
mod mac {
    #[link(name = "CoreGraphics", kind = "framework")]
    extern "C" {
        fn CGPreflightScreenCaptureAccess() -> bool;
        fn CGRequestScreenCaptureAccess() -> bool;
    }
    #[link(name = "ApplicationServices", kind = "framework")]
    extern "C" {
        fn AXIsProcessTrusted() -> bool;
    }
    pub fn screen() -> bool { unsafe { CGPreflightScreenCaptureAccess() } }
    pub fn request_screen() -> bool { unsafe { CGRequestScreenCaptureAccess() } }
    pub fn accessibility() -> bool { unsafe { AXIsProcessTrusted() } }
}

fn permissions() -> Permissions {
    #[cfg(target_os = "macos")]
    return Permissions { screen: Some(mac::screen()), accessibility: Some(mac::accessibility()) };
    #[cfg(not(target_os = "macos"))]
    Permissions { screen: None, accessibility: None }
}

#[tauri::command]
fn get_state(host: State<Arc<Host>>) -> UiState {
    let cfg = host.cfg.read().unwrap().clone();
    let (av, src) = host.av_server();
    let listen = host.listen.lock().unwrap().clone();
    UiState {
        version: VERSION.into(),
        demo: host.opts.demo,
        platform: host::PLATFORM,
        listening: listen.addrs.iter().map(|a| format!("http://{a}")).collect(),
        waiting_for_zerotier: listen.waiting_for_zerotier,
        error: listen.error,
        password: cfg.token,
        config_path: host.store.path.display().to_string(),
        av_server: av,
        av_source: src,
        av_server_setting: cfg.av_backup_server,
        roles: cfg.av_backup_roles,
        sessions: host.sessions.lock().unwrap().clone(),
        update: host.update.lock().unwrap().clone(),
        can_self_update: config::is_installed(),
        log: host.log.lock().unwrap().iter().rev().take(30).cloned().collect(),
        permissions: permissions(),
    }
}

#[tauri::command]
fn set_password(host: State<Arc<Host>>, password: Option<String>) -> Result<(), String> {
    let p = match password {
        None => config::new_password(),
        Some(p) if p.trim().chars().count() >= 6 => p.trim().to_string(),
        Some(_) => return Err("Use at least 6 characters.".into()),
    };
    host.update_cfg(|c| c.token = p)?;
    host.log("Access password changed");
    Ok(())
}

#[tauri::command]
fn set_av_backup(host: State<Arc<Host>>, server: String, roles: Vec<String>) -> Result<(), String> {
    let url = auth::normalize_server_url(&server);
    if !server.trim().is_empty() && url.is_empty() {
        return Err("That is not a server address. Example: http://10.7.0.5:8090".into());
    }
    let roles: Vec<String> = roles.into_iter().filter(|r| r == "admin" || r == "operator").collect();
    if roles.is_empty() {
        return Err("Allow at least one role.".into());
    }
    host.update_cfg(|c| { c.av_backup_server = url; c.av_backup_roles = roles; })?;
    host.log("AV Backup sign-in settings saved");
    Ok(())
}

#[tauri::command]
fn disconnect_all(host: State<Arc<Host>>) {
    let _ = host.kick.send(());
}

#[tauri::command]
fn autostart(app: AppHandle, enable: Option<bool>) -> Result<bool, String> {
    use tauri_plugin_autostart::ManagerExt;
    let m = app.autolaunch();
    match enable {
        Some(true) => m.enable().map_err(|e| e.to_string())?,
        Some(false) => m.disable().map_err(|e| e.to_string())?,
        None => {}
    }
    m.is_enabled().map_err(|e| e.to_string())
}

#[tauri::command]
fn open_link(app: AppHandle, which: String) -> Result<(), String> {
    use tauri_plugin_opener::OpenerExt;
    let url = match which.as_str() {
        "releases" => format!("https://github.com/{}/releases/latest", host::REPO),
        "screen" => "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture".into(),
        "accessibility" => "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility".into(),
        _ => return Err("unknown link".into()),
    };
    app.opener().open_url(url, None::<&str>).map_err(|e| e.to_string())
}

#[tauri::command]
fn request_screen_access() -> bool {
    #[cfg(target_os = "macos")]
    return mac::request_screen();
    #[cfg(not(target_os = "macos"))]
    true
}

#[tauri::command]
async fn check_update(host: State<'_, Arc<Host>>) -> Result<Option<UpdateInfo>, String> {
    let found = host::update::check_once(VERSION).await;
    *host.update.lock().unwrap() = found.clone();
    Ok(found)
}

/// Download, verify (signature) and install the new version, then restart.
#[tauri::command]
async fn install_update(app: AppHandle) -> Result<(), String> {
    use tauri_plugin_updater::UpdaterExt;
    let update = app.updater().map_err(|e| e.to_string())?
        .check().await.map_err(|e| format!("Update check failed: {e}"))?
        .ok_or("You already have the latest version.")?;
    update.download_and_install(|_, _| {}, || {}).await
        .map_err(|e| format!("Update failed: {e}"))?;
    app.restart();
}

fn show_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

pub fn run_desktop(a: Args) {
    let host = Host::new(a.opts);
    apply_av_server(&host, &a.av_server);
    banner(&host);
    let minimized = a.minimized;
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| show_main(app)))
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent, Some(vec!["--minimized"])))
        .manage(host.clone())
        .setup(move |app| {
            // macOS wants keyboard/mouse events from the main thread
            #[cfg(target_os = "macos")]
            {
                let handle = app.handle().clone();
                let _ = host.input.set(host::input::Input::on(host.input_factory(), move |f| {
                    let _ = handle.run_on_main_thread(f);
                }));
            }
            let show = MenuItem::with_id(app, "show", "Show ZT Remote Desktop", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit (stop remote access)", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &quit])?;
            TrayIconBuilder::with_id("tray")
                .icon(app.default_window_icon().cloned().expect("icon"))
                .tooltip("ZT Remote Desktop")
                .menu(&menu)
                .on_menu_event(|app, e| match e.id().as_ref() {
                    "show" => show_main(app),
                    "quit" => app.exit(0),
                    _ => {}
                })
                .build(app)?;
            tauri::async_runtime::spawn(host::server::run(host.clone()));
            tauri::async_runtime::spawn(host::update::run(host.clone()));
            if minimized {
                if let Some(w) = app.get_webview_window("main") { let _ = w.hide(); }
            }
            Ok(())
        })
        .on_window_event(|w, e| {
            // closing the window keeps remote access running in the tray
            if let WindowEvent::CloseRequested { api, .. } = e {
                api.prevent_close();
                let _ = w.hide();
            }
        })
        .invoke_handler(tauri::generate_handler![
            get_state, set_password, set_av_backup, disconnect_all, autostart,
            open_link, request_screen_access, check_update, install_update
        ])
        .run(tauri::generate_context!())
        .expect("error while running ZT Remote Desktop");
}
