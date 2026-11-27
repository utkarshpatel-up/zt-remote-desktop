//! HTTP + WebSocket server. Same protocol as v1, so the phone page and the
//! tests in tests/ work unchanged.

use super::{auth, Host, Session, PLATFORM, VERSION};
use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::{ConnectInfo, State};
use axum::response::{Html, IntoResponse, Json};
use axum::routing::get;
use axum::Router;
use serde_json::{json, Value};
use std::net::{IpAddr, Ipv4Addr, SocketAddr};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::sync::mpsc;

const VIEWER: &str = include_str!("../../../ui/viewer.html");
static NEXT_ID: AtomicU64 = AtomicU64::new(1);

/// Decide where to listen, then serve until the process ends.
/// "auto" = ZeroTier only, waiting for it if it isn't up yet (never falls
/// back to the LAN); "all" = every interface; or one IP address.
pub async fn run(host: Arc<Host>) {
    let ips: Vec<IpAddr> = match host.opts.bind.as_str() {
        "all" => vec![IpAddr::V4(Ipv4Addr::UNSPECIFIED)],
        "auto" => {
            let mut noted: Option<Instant> = None;
            loop {
                let ips = super::net::zerotier_ips();
                if !ips.is_empty() {
                    break ips.into_iter().map(IpAddr::V4).collect();
                }
                host.listen.lock().unwrap().waiting_for_zerotier = true;
                if noted.map_or(true, |t| t.elapsed() > Duration::from_secs(60)) {
                    host.log("Waiting for ZeroTier to connect... (listening only on ZeroTier)");
                    noted = Some(Instant::now());
                }
                tokio::time::sleep(Duration::from_secs(3)).await;
            }
        }
        ip => match ip.parse() {
            Ok(ip) => vec![ip],
            Err(_) => {
                host.listen.lock().unwrap().error = Some(format!("not an IP address: {ip}"));
                return;
            }
        },
    };
    let port = host.port();
    let app = Router::new()
        .route("/", get(|| async { Html(VIEWER) }))
        .route("/health", get(health))
        .route("/ws", get(ws_upgrade))
        .with_state(host.clone());
    let mut tasks = Vec::new();
    for ip in ips {
        let addr = SocketAddr::new(ip, port);
        match tokio::net::TcpListener::bind(addr).await {
            Ok(l) => {
                {
                    let mut st = host.listen.lock().unwrap();
                    st.waiting_for_zerotier = false;
                    st.addrs.push(addr.to_string());
                }
                host.log(format!("Listening on http://{addr}"));
                let svc = app.clone().into_make_service_with_connect_info::<SocketAddr>();
                tasks.push(tokio::spawn(async move { let _ = axum::serve(l, svc).await; }));
            }
            Err(e) => {
                host.listen.lock().unwrap().error = Some(format!("cannot listen on {addr}: {e}"));
                host.log(format!("Cannot listen on {addr}: {e}"));
            }
        }
    }
    for t in tasks {
        let _ = t.await;
    }
}

async fn health(State(host): State<Arc<Host>>) -> impl IntoResponse {
    Json(json!({
        "ok": true, "demo": host.opts.demo, "version": VERSION,
        "av_backup": !host.av_server().0.is_empty(),
    }))
}

async fn ws_upgrade(ws: WebSocketUpgrade, ConnectInfo(addr): ConnectInfo<SocketAddr>,
                    State(host): State<Arc<Host>>) -> impl IntoResponse {
    ws.max_message_size(1 << 20).on_upgrade(move |s| session(s, addr.ip(), host))
}

/// First message must sign in. Returns who it is, or the reason code.
async fn authenticate(host: &Host, ip: IpAddr, first: Option<Message>) -> Result<String, &'static str> {
    if host.limiter.blocked(ip) {
        return Err("too_many_attempts");
    }
    let Some(Message::Text(t)) = first else { return Err("wrong_password") };
    let msg: Value = serde_json::from_str(t.as_str()).unwrap_or(Value::Null);
    if msg["type"] != "auth" {
        return Err("wrong_password");
    }
    if msg["method"] == "av_backup" {
        let (server, _) = host.av_server();
        let email = msg["email"].as_str().unwrap_or("").trim().to_string();
        let password = msg["password"].as_str().unwrap_or("").to_string();
        if server.is_empty() {
            return Err("av_backup_not_set_up");
        }
        if email.is_empty() || password.is_empty() {
            return Err("wrong_credentials");
        }
        let roles = host.cfg.read().unwrap().av_backup_roles.clone();
        return auth::verify_av_backup(&server, &email, &password, &roles).await;
    }
    let given = msg["token"].as_str().unwrap_or("");
    let token = host.cfg.read().unwrap().token.clone();
    use subtle::ConstantTimeEq;
    if given.len() == token.len() && bool::from(given.as_bytes().ct_eq(token.as_bytes())) {
        Ok("access password".into())
    } else {
        Err("wrong_password")
    }
}

#[derive(Clone, Copy)]
struct StreamSettings { fps: u32, quality: u8, max_width: u32 }

enum Out { Text(String), Bin(Vec<u8>) }

async fn session(mut ws: WebSocket, ip: IpAddr, host: Arc<Host>) {
    let first = match tokio::time::timeout(Duration::from_secs(15), ws.recv()).await {
        Ok(Some(Ok(m))) => Some(m),
        Ok(_) => None,
        Err(_) => return,
    };
    let who = match authenticate(&host, ip, first).await {
        Ok(who) => who,
        Err(reason) => {
            if matches!(reason, "wrong_password" | "wrong_credentials" | "not_member" | "role_not_allowed") {
                host.limiter.failed(ip);
            }
            host.log(format!("[auth] refused {ip}: {reason}"));
            let _ = ws.send(Message::Text(json!({"type": "auth_failed", "reason": reason}).to_string().into())).await;
            let _ = ws.send(Message::Close(None)).await;
            return;
        }
    };
    host.limiter.ok(ip);
    host.log(format!("[auth] {ip} connected ({who})"));
    let update = host.update.lock().unwrap().clone();
    let ok = json!({"type": "auth_ok", "demo": host.opts.demo, "version": VERSION,
                    "update": update, "platform": PLATFORM});
    if ws.send(Message::Text(ok.to_string().into())).await.is_err() {
        return;
    }

    let id = NEXT_ID.fetch_add(1, Ordering::Relaxed);
    let since = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
    host.sessions.lock().unwrap().push(Session { id, ip: ip.to_string(), who: who.clone(), since });

    let settings = Arc::new(Mutex::new(StreamSettings { fps: 12, quality: 60, max_width: 1280 }));
    let stop = Arc::new(AtomicBool::new(false));
    let (tx, mut rx) = mpsc::channel::<Out>(2);
    {
        let (host, settings, stop) = (host.clone(), settings.clone(), stop.clone());
        std::thread::Builder::new().name(format!("capture-{id}"))
            .spawn(move || capture_loop(host, settings, stop, tx)).ok();
    }

    let mut kick = host.kick.subscribe();
    let mut ping = tokio::time::interval(Duration::from_secs(20));
    let mut last_seen = Instant::now();
    loop {
        tokio::select! {
            out = rx.recv() => {
                let r = match out {
                    Some(Out::Text(s)) => ws.send(Message::Text(s.into())).await,
                    Some(Out::Bin(b)) => ws.send(Message::Binary(b.into())).await,
                    None => break,
                };
                if r.is_err() { break; }
            }
            msg = ws.recv() => {
                last_seen = Instant::now();
                match msg {
                    Some(Ok(Message::Text(t))) => {
                        if let Ok(v) = serde_json::from_str::<Value>(t.as_str()) {
                            if v.is_object() { handle(&host, &settings, &v); }
                        }
                    }
                    Some(Ok(Message::Close(_))) | None | Some(Err(_)) => break,
                    _ => {}
                }
            }
            _ = kick.recv() => {
                let _ = ws.send(Message::Close(None)).await;
                break;
            }
            _ = ping.tick() => {
                // notice a phone that dropped off ZeroTier even on a still screen
                if last_seen.elapsed() > Duration::from_secs(60) { break; }
                if ws.send(Message::Ping(Vec::new().into())).await.is_err() { break; }
            }
        }
    }
    stop.store(true, Ordering::Relaxed);
    host.sessions.lock().unwrap().retain(|s| s.id != id);
    host.log(format!("[auth] {ip} disconnected ({who})"));
}

fn num(v: &Value, default: f64, lo: f64, hi: f64) -> f64 {
    let x = v.as_f64().or_else(|| v.as_str().and_then(|s| s.parse().ok())).unwrap_or(default);
    if x.is_finite() { x.clamp(lo, hi) } else { default }
}

fn strings(v: &Value) -> Vec<String> {
    v.as_array().map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect()).unwrap_or_default()
}

fn handle(host: &Host, settings: &Mutex<StreamSettings>, m: &Value) {
    let input = host.input();
    match m["type"].as_str().unwrap_or("") {
        "config" => {
            let mut s = settings.lock().unwrap();
            if !m["fps"].is_null() { s.fps = num(&m["fps"], s.fps as f64, 1.0, 30.0) as u32; }
            if !m["quality"].is_null() { s.quality = num(&m["quality"], s.quality as f64, 10.0, 95.0) as u8; }
            if !m["max_width"].is_null() {
                s.max_width = num(&m["max_width"], s.max_width as f64, 0.0, 3840.0) as u32;
                if s.max_width != 0 && s.max_width < 320 { s.max_width = 320; }   // 0 = original
            }
        }
        "mouse_move" => {
            let (x, y) = (num(&m["x"], 0.0, 0.0, 1.0), num(&m["y"], 0.0, 0.0, 1.0));
            input.run(move |c| { let (w, h) = c.size(); c.move_to(x * w, y * h); });
        }
        "mouse_move_rel" => {
            // trackpad mode: dx/dy are fractions of the screen size
            let (dx, dy) = (num(&m["dx"], 0.0, -1.0, 1.0), num(&m["dy"], 0.0, -1.0, 1.0));
            input.run(move |c| {
                let (w, h) = c.size();
                if w <= 0.0 || h <= 0.0 { return; }
                if let Some((x, y)) = c.location() {
                    c.move_to((x + dx * w).clamp(0.0, w - 1.0), (y + dy * h).clamp(0.0, h - 1.0));
                }
            });
        }
        "mouse_down" => {
            // without x/y the button goes down where the pointer already is
            let at = (!m["x"].is_null() && !m["y"].is_null())
                .then(|| (num(&m["x"], 0.0, 0.0, 1.0), num(&m["y"], 0.0, 0.0, 1.0)));
            let b = m["button"].as_str().unwrap_or("left").to_string();
            input.run(move |c| {
                if let Some((x, y)) = at { let (w, h) = c.size(); c.move_to(x * w, y * h); }
                c.button(&b, true);
            });
        }
        "mouse_up" => {
            let b = m["button"].as_str().unwrap_or("left").to_string();
            input.run(move |c| c.button(&b, false));
        }
        "scroll" => {
            let (dx, dy) = (num(&m["dx"], 0.0, -50.0, 50.0) as i32, num(&m["dy"], 0.0, -50.0, 50.0) as i32);
            input.run(move |c| c.scroll(dx, dy));
        }
        "key" => {
            let k = m["key"].as_str().unwrap_or("").to_string();
            let mods = strings(&m["modifiers"]);
            input.run(move |c| c.key_tap(&k, &mods));
        }
        "key_down" => {
            let k = m["key"].as_str().unwrap_or("").to_string();
            input.run(move |c| c.key_down(&k));
        }
        "key_up" => {
            let k = m["key"].as_str().unwrap_or("").to_string();
            input.run(move |c| c.key_up(&k));
        }
        "text" => {
            let t: String = m["text"].as_str().unwrap_or("").chars().take(10_000).collect();
            input.run(move |c| c.text(&t));
        }
        _ => {}
    }
}

fn r4(v: f64) -> f64 { (v * 10000.0).round() / 10000.0 }

/// One thread per viewer: capture, encode, hand to the socket. A still
/// screen sends nothing; meta (size, pointer) only when it changes.
fn capture_loop(host: Arc<Host>, settings: Arc<Mutex<StreamSettings>>, stop: Arc<AtomicBool>,
                tx: mpsc::Sender<Out>) {
    let mut cap = None;
    let mut last_meta = Value::Null;
    while !stop.load(Ordering::Relaxed) {
        let t0 = Instant::now();
        let s = *settings.lock().unwrap();
        if cap.is_none() {
            match host.new_capture() {
                Ok(c) => cap = Some(c),
                Err(e) => {
                    if tx.blocking_send(Out::Text(json!({"type": "error", "message": e}).to_string())).is_err() { return; }
                    std::thread::sleep(Duration::from_secs(2));
                    continue;
                }
            }
        }
        let frame = cap.as_mut().unwrap().frame(s.quality, s.max_width);
        let pointer = host.input().query(|c| (c.size(), c.location()));
        let mut meta = json!({"type": "meta"});
        if let Some(((w, h), loc)) = pointer {
            meta["screen_w"] = json!(w as u32);
            meta["screen_h"] = json!(h as u32);
            if let (Some((x, y)), true) = (loc, w > 0.0 && h > 0.0) {
                meta["cx"] = json!(r4((x / w).clamp(0.0, 1.0)));
                meta["cy"] = json!(r4((y / h).clamp(0.0, 1.0)));
            }
        }
        if meta != last_meta {
            if tx.blocking_send(Out::Text(meta.to_string())).is_err() { return; }
            last_meta = meta;
        }
        match frame {
            Ok(Some(jpeg)) => { if tx.blocking_send(Out::Bin(jpeg)).is_err() { return; } }
            Ok(None) => {}
            Err(e) => {
                // capture hiccup: report, keep going
                if tx.blocking_send(Out::Text(json!({"type": "error", "message": e}).to_string())).is_err() { return; }
                std::thread::sleep(Duration::from_millis(500));
            }
        }
        let budget = Duration::from_secs_f64(1.0 / s.fps.max(1) as f64);
        if let Some(rest) = budget.checked_sub(t0.elapsed()) {
            std::thread::sleep(rest);
        }
    }
}
