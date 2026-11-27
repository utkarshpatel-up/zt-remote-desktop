//! "Is there a newer version?" Reads latest.json from the latest GitHub
//! release (the same file the in-app updater uses) at start and every 12 h.

use super::{Host, UpdateInfo, REPO, VERSION};
use std::sync::Arc;
use std::time::Duration;

pub fn parse_version(v: &str) -> Option<(u64, u64, u64)> {
    let v = v.trim().trim_start_matches('v');
    let mut it = v.split('.');
    let a = it.next()?.parse().ok()?;
    let b = it.next()?.parse().ok()?;
    let c = match it.next() { Some(x) => x.parse().ok()?, None => 0 };
    if it.next().is_some() { return None; }
    Some((a, b, c))
}

fn feed_url() -> String {
    std::env::var("ZT_UPDATE_URL")
        .unwrap_or_else(|_| format!("https://github.com/{REPO}/releases/latest/download/latest.json"))
}

pub async fn check_once(current: &str) -> Option<UpdateInfo> {
    let client = reqwest::Client::builder().timeout(Duration::from_secs(15))
        .user_agent("zt-remote-desktop-update-check").build().ok()?;
    let r = client.get(feed_url()).send().await.ok()?;
    if !r.status().is_success() { return None; }
    let v: serde_json::Value = r.json().await.ok()?;
    let latest = parse_version(v["version"].as_str()?)?;
    if latest <= parse_version(current)? { return None; }
    Some(UpdateInfo {
        version: format!("{}.{}.{}", latest.0, latest.1, latest.2),
        page: format!("https://github.com/{REPO}/releases/latest"),
    })
}

pub async fn run(host: Arc<Host>) {
    loop {
        if host.cfg.read().unwrap().check_updates {
            let found = check_once(VERSION).await;
            let mut cur = host.update.lock().unwrap();
            if found.is_some() && *cur != found {
                let f = found.as_ref().unwrap();
                host.log(format!("Update available: v{} (you have v{VERSION}) - {}", f.version, f.page));
            }
            if found.is_some() || cur.is_none() {
                *cur = found;
            }
        }
        tokio::time::sleep(Duration::from_secs(12 * 3600)).await;
    }
}

#[cfg(test)]
mod tests {
    use super::parse_version;
    #[test]
    fn versions() {
        assert_eq!(parse_version("v1.2.3"), Some((1, 2, 3)));
        assert_eq!(parse_version("1.10"), Some((1, 10, 0)));
        assert_eq!(parse_version("latest"), None);
        assert!(parse_version("1.10.0") > parse_version("1.9.9"));
    }
}
