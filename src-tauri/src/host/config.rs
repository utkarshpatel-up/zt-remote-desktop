//! zt-remote-config.json: access password, port, AV Backup server, roles.

use rand::{distributions::Alphanumeric, Rng};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

pub const DEFAULT_PORT: u16 = 8765;
pub const FILE_NAME: &str = "zt-remote-config.json";

fn default_port() -> u16 { DEFAULT_PORT }
fn default_roles() -> Vec<String> { vec!["admin".into(), "operator".into()] }
fn yes() -> bool { true }

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Config {
    /// the access password (named "token" for compatibility with v1 configs)
    #[serde(default)]
    pub token: String,
    #[serde(default = "default_port")]
    pub port: u16,
    /// "" = use the AV Backup app's server if it is installed on this computer
    #[serde(default)]
    pub av_backup_server: String,
    #[serde(default = "default_roles")]
    pub av_backup_roles: Vec<String>,
    #[serde(default = "yes")]
    pub check_updates: bool,
}

impl Default for Config {
    fn default() -> Self {
        Config {
            token: String::new(),
            port: DEFAULT_PORT,
            av_backup_server: String::new(),
            av_backup_roles: default_roles(),
            check_updates: true,
        }
    }
}

pub fn new_password() -> String {
    rand::thread_rng().sample_iter(&Alphanumeric).take(20).map(char::from).collect()
}

/// Is this a Windows install made by the installer (it can update itself)?
/// A portable copy has no uninstall.exe next to it.
pub fn is_installed() -> bool {
    if cfg!(target_os = "windows") {
        exe_dir().map(|d| d.join("uninstall.exe").exists()).unwrap_or(false)
    } else {
        cfg!(target_os = "macos")
    }
}

fn exe_dir() -> Option<PathBuf> {
    std::env::current_exe().ok()?.parent().map(Path::to_path_buf)
}

/// Portable Windows copies keep their config next to the .exe; everything
/// else uses the per-user config folder (it survives updates).
pub fn default_dir() -> PathBuf {
    if cfg!(target_os = "windows") && !is_installed() {
        if let Some(d) = exe_dir() {
            return d;
        }
    }
    dirs::config_dir().unwrap_or_else(|| PathBuf::from(".")).join("ZT Remote Desktop")
}

pub struct Store {
    pub path: PathBuf,
}

impl Store {
    pub fn new(dir: &Path) -> Self {
        Store { path: dir.join(FILE_NAME) }
    }

    /// Load (filling in anything missing, e.g. a first-run password) and save back if changed.
    pub fn load(&self) -> Config {
        let loaded: Option<Config> = std::fs::read_to_string(&self.path)
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok());
        let mut cfg = loaded.clone().unwrap_or_default();
        if cfg.token.trim().is_empty() {
            cfg.token = new_password();
        }
        if cfg.port == 0 {
            cfg.port = DEFAULT_PORT;
        }
        if loaded.as_ref() != Some(&cfg) {
            let _ = self.save(&cfg);
        }
        cfg
    }

    pub fn save(&self, cfg: &Config) -> std::io::Result<()> {
        if let Some(d) = self.path.parent() {
            std::fs::create_dir_all(d)?;
        }
        let tmp = self.path.with_extension("json.tmp");
        std::fs::write(&tmp, serde_json::to_string_pretty(cfg).unwrap_or_default())?;
        std::fs::rename(&tmp, &self.path)
    }
}
