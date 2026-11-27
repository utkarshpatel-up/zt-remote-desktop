//! ZT Remote Desktop. On Windows/macOS this app is the host (the computer you
//! control). The phone uses the page the host serves; an Android / iOS build
//! of this same app can become the viewer later (see `run` on mobile).

#[cfg(desktop)]
mod host;
#[cfg(desktop)]
mod app;

#[cfg(desktop)]
pub fn run() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let opts = match app::parse_args(&args) {
        Ok(o) => o,
        Err(msg) => {
            println!("{msg}");
            std::process::exit(if msg.starts_with("ZT Remote Desktop") { 0 } else { 2 });
        }
    };
    if opts.headless {
        app::run_headless(opts);
    } else {
        app::run_desktop(opts);
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
#[cfg(mobile)]
pub fn run() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("error while running ZT Remote Desktop");
}
