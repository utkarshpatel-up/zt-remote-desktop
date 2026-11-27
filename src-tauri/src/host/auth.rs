//! Sign-in: the access password, or an AV Backup team account checked live
//! against the team's PocketBase server (auth-with-password, then avb_me).

use super::config::Config;
use reqwest::Url;
use std::collections::HashMap;
use std::net::IpAddr;
use std::sync::Mutex;
use std::time::{Duration, Instant};

/// "10.7.0.5:8090" -> "http://10.7.0.5:8090". "" when unusable.
pub fn normalize_server_url(url: &str) -> String {
    let mut u = url.trim().trim_end_matches('/').to_string();
    if u.is_empty() {
        return String::new();
    }
    if !u.contains("://") {
        u = format!("http://{u}");
    }
    match Url::parse(&u) {
        Ok(p) if (p.scheme() == "http" || p.scheme() == "https")
            && p.host_str().is_some()
            && p.username().is_empty()
            && p.password().is_none()
            && p.query().is_none()
            && p.fragment().is_none() => u,
        _ => String::new(),
    }
}

/// Server from our config first, else from the AV Backup app on this computer.
pub fn find_av_backup_server(cfg: &Config) -> (String, &'static str) {
    let url = normalize_server_url(&cfg.av_backup_server);
    if !url.is_empty() {
        return (url, "this app's settings");
    }
    if let Some(base) = dirs::config_dir() {
        for name in ["AV Backup", "av-backup"] {
            let p = base.join(name).join("sync-config.json");
            if let Ok(s) = std::fs::read_to_string(p) {
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(&s) {
                    let url = normalize_server_url(v["serverUrl"].as_str().unwrap_or(""));
                    if !url.is_empty() {
                        return (url, "AV Backup app");
                    }
                }
            }
        }
    }
    (String::new(), "")
}

/// Returns the member's display name, or a reason code the phone understands.
/// The password goes to the team server only; it is never stored or logged.
pub async fn verify_av_backup(server: &str, email: &str, password: &str, roles: &[String])
    -> Result<String, &'static str>
{
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(15))
        .redirect(reqwest::redirect::Policy::none()) // never carry a token elsewhere
        .build()
        .map_err(|_| "server_error")?;
    let r = client
        .post(format!("{server}/api/collections/users/auth-with-password"))
        .json(&serde_json::json!({ "identity": email, "password": password }))
        .send().await.map_err(|_| "server_unreachable")?;
    match r.status().as_u16() {
        200 => {}
        400 | 401 | 403 | 404 => return Err("wrong_credentials"),
        _ => return Err("server_error"),
    }
    let body: serde_json::Value = r.json().await.map_err(|_| "server_error")?;
    let token = body["token"].as_str().filter(|t| !t.is_empty()).ok_or("server_error")?;
    let r = client
        .post(format!("{server}/api/avb/rpc/avb_me"))
        .header("Authorization", token)
        .json(&serde_json::json!({}))
        .send().await.map_err(|_| "server_unreachable")?;
    match r.status().as_u16() {
        200 => {}
        401 | 403 => return Err("not_member"),
        _ => return Err("server_error"),
    }
    let me: serde_json::Value = r.json().await.map_err(|_| "server_error")?;
    let role = me["role"].as_str().unwrap_or("");
    if !roles.iter().any(|r| r == role) {
        return Err("role_not_allowed");
    }
    Ok(me["display_name"].as_str().filter(|s| !s.is_empty()).unwrap_or(email).to_string())
}

/// Slows down password guessing: 5 failures from one address -> 60 s wait.
pub struct Limiter {
    inner: Mutex<HashMap<IpAddr, (Vec<Instant>, Option<Instant>)>>,
}

const MAX_FAILS: usize = 5;
const WINDOW: Duration = Duration::from_secs(300);
const LOCKOUT: Duration = Duration::from_secs(60);

impl Limiter {
    pub fn new() -> Self { Limiter { inner: Mutex::new(HashMap::new()) } }

    pub fn blocked(&self, ip: IpAddr) -> bool {
        let m = self.inner.lock().unwrap();
        matches!(m.get(&ip), Some((_, Some(until))) if *until > Instant::now())
    }

    pub fn failed(&self, ip: IpAddr) {
        let mut m = self.inner.lock().unwrap();
        let now = Instant::now();
        let e = m.entry(ip).or_insert_with(|| (Vec::new(), None));
        e.0.retain(|t| now.duration_since(*t) < WINDOW);
        e.0.push(now);
        if e.0.len() >= MAX_FAILS {
            e.1 = Some(now + LOCKOUT);
            e.0.clear();
        }
    }

    pub fn ok(&self, ip: IpAddr) {
        self.inner.lock().unwrap().remove(&ip);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn urls() {
        assert_eq!(normalize_server_url("10.7.0.5:8090/"), "http://10.7.0.5:8090");
        assert_eq!(normalize_server_url("https://x.y"), "https://x.y");
        assert_eq!(normalize_server_url("http://a:b@h"), "");
        assert_eq!(normalize_server_url("ftp://h"), "");
        assert_eq!(normalize_server_url("  "), "");
    }
    #[test]
    fn limiter() {
        let l = Limiter::new();
        let ip: IpAddr = "10.0.0.1".parse().unwrap();
        for _ in 0..4 { l.failed(ip); }
        assert!(!l.blocked(ip));
        l.failed(ip);
        assert!(l.blocked(ip));
        assert!(!l.blocked("10.0.0.2".parse().unwrap()));
    }
}
