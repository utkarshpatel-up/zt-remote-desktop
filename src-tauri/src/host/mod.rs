//! The host: the computer being controlled. Serves the phone page and the
//! WebSocket (screen out, mouse/keyboard in) on the ZeroTier address only.

pub mod auth;
pub mod capture;
pub mod config;
pub mod input;
pub mod net;
pub mod server;
pub mod update;

use serde::Serialize;
use std::collections::VecDeque;
use std::path::PathBuf;
use std::sync::{Arc, Mutex, OnceLock, RwLock};

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
pub const REPO: &str = "utkarshpatel-up/zt-remote-desktop";

pub const PLATFORM: &str = if cfg!(target_os = "macos") {
    "mac"
} else if cfg!(target_os = "windows") {
    "windows"
} else {
    "linux"
};

pub struct DemoState {
    pub frames: u64,
    pub cursor: (f64, f64),
    pub screen_w: u32,
    pub screen_h: u32,
    pub still: bool,
    pub last_event: String,
}

#[derive(Clone, Serialize)]
pub struct Session {
    pub id: u64,
    pub ip: String,
    pub who: String,
    pub since: u64,
}

#[derive(Clone, Serialize, Default)]
pub struct Listen {
    /// addresses being listened on, e.g. "10.7.134.5:8765"
    pub addrs: Vec<String>,
    pub waiting_for_zerotier: bool,
    pub error: Option<String>,
}

#[derive(Clone, Serialize, PartialEq, Debug)]
pub struct UpdateInfo {
    pub version: String,
    pub page: String,
}

pub struct Options {
    pub demo: bool,
    pub demo_still: bool,
    pub bind: String,
    pub port: Option<u16>,
    pub config_dir: PathBuf,
}

pub struct Host {
    pub opts: Options,
    pub store: config::Store,
    pub cfg: RwLock<config::Config>,
    pub limiter: auth::Limiter,
    pub demo: Arc<Mutex<DemoState>>,
    pub input: OnceLock<input::Input>,
    pub sessions: Mutex<Vec<Session>>,
    pub kick: tokio::sync::broadcast::Sender<()>,
    pub listen: Mutex<Listen>,
    pub update: Mutex<Option<UpdateInfo>>,
    pub log: Mutex<VecDeque<String>>,
}

impl Host {
    pub fn new(opts: Options) -> Arc<Self> {
        let store = config::Store::new(&opts.config_dir);
        let cfg = store.load();
        let demo = Arc::new(Mutex::new(DemoState {
            frames: 0,
            cursor: (640.0, 360.0),
            screen_w: 1280,
            screen_h: 720,
            still: opts.demo_still,
            last_event: "demo ready".into(),
        }));
        Arc::new(Host {
            opts,
            store,
            cfg: RwLock::new(cfg),
            limiter: auth::Limiter::new(),
            demo,
            input: OnceLock::new(),
            sessions: Mutex::new(Vec::new()),
            kick: tokio::sync::broadcast::channel(4).0,
            listen: Mutex::new(Listen::default()),
            update: Mutex::new(None),
            log: Mutex::new(VecDeque::new()),
        })
    }

    pub fn port(&self) -> u16 {
        self.opts.port.unwrap_or_else(|| self.cfg.read().unwrap().port)
    }

    /// What builds the mouse/keyboard controller on the input thread.
    pub fn input_factory(&self) -> input::Factory {
        if self.opts.demo {
            let d = self.demo.clone();
            Arc::new(move || Some(Box::new(input::DemoCtl(d.clone())) as Box<dyn input::Ctl>))
        } else {
            Arc::new(input::RealCtl::new)
        }
    }

    pub fn input(&self) -> &input::Input {
        self.input.get_or_init(|| input::Input::spawn(self.input_factory()))
    }

    pub fn new_capture(&self) -> Result<Box<dyn capture::Capture>, String> {
        if self.opts.demo {
            Ok(Box::new(capture::DemoCapture::new(self.demo.clone())))
        } else {
            Ok(Box::new(capture::RealCapture::new()?))
        }
    }

    pub fn av_server(&self) -> (String, &'static str) {
        auth::find_av_backup_server(&self.cfg.read().unwrap())
    }

    pub fn update_cfg(&self, f: impl FnOnce(&mut config::Config)) -> Result<(), String> {
        let mut c = self.cfg.write().unwrap();
        let mut next = c.clone();
        f(&mut next);
        self.store.save(&next).map_err(|e| format!("could not save settings: {e}"))?;
        *c = next;
        Ok(())
    }

    pub fn log(&self, line: impl Into<String>) {
        let line = line.into();
        println!("{line}");
        let mut l = self.log.lock().unwrap();
        l.push_back(line);
        while l.len() > 100 {
            l.pop_front();
        }
    }
}
