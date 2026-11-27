//! Mouse and keyboard. All input runs on one thread that owns the controller
//! (on macOS that is the app's main thread, which the system requires for
//! keyboard-layout lookups).

use super::DemoState;
use enigo::{Axis, Button, Coordinate, Direction, Enigo, Key, Keyboard, Mouse, Settings};
use std::sync::{mpsc, Arc, Mutex};
use std::time::Duration;

pub trait Ctl {
    /// the coordinate space mouse moves use (logical points on macOS)
    fn size(&mut self) -> (f64, f64);
    fn location(&mut self) -> Option<(f64, f64)>;
    fn move_to(&mut self, x: f64, y: f64);
    fn button(&mut self, button: &str, down: bool);
    /// dy > 0 scrolls up, dx > 0 scrolls right (like the phone sends)
    fn scroll(&mut self, dx: i32, dy: i32);
    fn key_tap(&mut self, key: &str, modifiers: &[String]);
    fn key_down(&mut self, key: &str);
    fn key_up(&mut self, key: &str);
    fn text(&mut self, text: &str);
}

pub type Job = Box<dyn FnOnce(&mut dyn Ctl) + Send>;
pub type Factory = Arc<dyn Fn() -> Option<Box<dyn Ctl>> + Send + Sync>;

thread_local! {
    static CTL: std::cell::RefCell<Option<Box<dyn Ctl>>> = const { std::cell::RefCell::new(None) };
}

fn with_ctl(factory: &Factory, job: Job) {
    CTL.with(|c| {
        let mut c = c.borrow_mut();
        if c.is_none() {
            *c = factory();
        }
        if let Some(ctl) = c.as_mut() {
            job(ctl.as_mut());
        }
    })
}

/// Runs jobs on the input thread. `spawn` gives it its own thread;
/// `on(runner)` hands each job to something else (the macOS main thread).
#[derive(Clone)]
pub struct Input {
    send: Arc<dyn Fn(Job) + Send + Sync>,
}

impl Input {
    pub fn spawn(factory: Factory) -> Self {
        let (tx, rx) = mpsc::channel::<Job>();
        std::thread::Builder::new()
            .name("input".into())
            .spawn(move || {
                for job in rx {
                    with_ctl(&factory, job);
                }
            })
            .expect("input thread");
        let tx = Mutex::new(tx);
        Input { send: Arc::new(move |j| { let _ = tx.lock().unwrap().send(j); }) }
    }

    #[cfg_attr(not(target_os = "macos"), allow(dead_code))]
    pub fn on(factory: Factory, runner: impl Fn(Box<dyn FnOnce() + Send>) + Send + Sync + 'static) -> Self {
        Input {
            send: Arc::new(move |j: Job| {
                let f = factory.clone();
                runner(Box::new(move || with_ctl(&f, j)));
            }),
        }
    }

    pub fn run(&self, job: impl FnOnce(&mut dyn Ctl) + Send + 'static) {
        (self.send)(Box::new(job));
    }

    /// Ask the input thread something and wait (up to 1 s) for the answer.
    pub fn query<T: Send + 'static>(&self, f: impl FnOnce(&mut dyn Ctl) -> T + Send + 'static) -> Option<T> {
        let (tx, rx) = mpsc::channel();
        self.run(move |c| { let _ = tx.send(f(c)); });
        rx.recv_timeout(Duration::from_secs(1)).ok()
    }
}

/// KeyboardEvent.key names and the phone's key-bar names -> keys.
pub fn map_key(name: &str) -> Option<Key> {
    let n = name.to_lowercase();
    let k = match n.as_str() {
        "enter" | "return" => Key::Return,
        "esc" | "escape" => Key::Escape,
        "tab" => Key::Tab,
        "backspace" => Key::Backspace,
        "delete" | "del" => Key::Delete,
        "space" | " " => Key::Space,
        "up" | "arrowup" => Key::UpArrow,
        "down" | "arrowdown" => Key::DownArrow,
        "left" | "arrowleft" => Key::LeftArrow,
        "right" | "arrowright" => Key::RightArrow,
        "home" => Key::Home,
        "end" => Key::End,
        "pageup" => Key::PageUp,
        "pagedown" => Key::PageDown,
        "ctrl" | "control" => Key::Control,
        "alt" => Key::Alt,
        "shift" => Key::Shift,
        "cmd" | "win" | "command" | "meta" | "os" => Key::Meta,
        "capslock" => Key::CapsLock,
        #[cfg(not(target_os = "macos"))]
        "insert" => Key::Insert,
        "f1" => Key::F1, "f2" => Key::F2, "f3" => Key::F3, "f4" => Key::F4,
        "f5" => Key::F5, "f6" => Key::F6, "f7" => Key::F7, "f8" => Key::F8,
        "f9" => Key::F9, "f10" => Key::F10, "f11" => Key::F11, "f12" => Key::F12,
        _ => {
            let mut chars = n.chars();
            match (chars.next(), chars.next()) {
                (Some(c), None) => Key::Unicode(c),
                _ => return None,
            }
        }
    };
    Some(k)
}

pub struct RealCtl {
    enigo: Enigo,
}

impl RealCtl {
    pub fn new() -> Option<Box<dyn Ctl>> {
        let settings = Settings { open_prompt_to_get_permissions: true, ..Default::default() };
        match Enigo::new(&settings) {
            Ok(enigo) => Some(Box::new(RealCtl { enigo })),
            Err(e) => {
                eprintln!("[input] cannot control mouse/keyboard: {e}");
                None
            }
        }
    }
}

impl Ctl for RealCtl {
    fn size(&mut self) -> (f64, f64) {
        self.enigo.main_display().map(|(w, h)| (w as f64, h as f64)).unwrap_or((0.0, 0.0))
    }
    fn location(&mut self) -> Option<(f64, f64)> {
        self.enigo.location().ok().map(|(x, y)| (x as f64, y as f64))
    }
    fn move_to(&mut self, x: f64, y: f64) {
        let _ = self.enigo.move_mouse(x.round() as i32, y.round() as i32, Coordinate::Abs);
    }
    fn button(&mut self, button: &str, down: bool) {
        let b = match button { "right" => Button::Right, "middle" => Button::Middle, _ => Button::Left };
        let _ = self.enigo.button(b, if down { Direction::Press } else { Direction::Release });
    }
    fn scroll(&mut self, dx: i32, dy: i32) {
        if dy != 0 { let _ = self.enigo.scroll(-dy, Axis::Vertical); }
        if dx != 0 { let _ = self.enigo.scroll(dx, Axis::Horizontal); }
    }
    fn key_tap(&mut self, key: &str, modifiers: &[String]) {
        let Some(k) = map_key(key) else { return };
        let mods: Vec<Key> = modifiers.iter().filter_map(|m| map_key(m)).collect();
        for m in &mods { let _ = self.enigo.key(*m, Direction::Press); }
        let _ = self.enigo.key(k, Direction::Click);
        for m in mods.iter().rev() { let _ = self.enigo.key(*m, Direction::Release); }
    }
    fn key_down(&mut self, key: &str) {
        if let Some(k) = map_key(key) { let _ = self.enigo.key(k, Direction::Press); }
    }
    fn key_up(&mut self, key: &str) {
        if let Some(k) = map_key(key) { let _ = self.enigo.key(k, Direction::Release); }
    }
    fn text(&mut self, text: &str) {
        let _ = self.enigo.text(text);
    }
}

/// Logs input instead of touching anything (demo mode).
pub struct DemoCtl(pub Arc<Mutex<DemoState>>);

impl DemoCtl {
    fn log(&self, what: String) {
        println!("[demo input] {what}");
        self.0.lock().unwrap().last_event = what;
    }
}

impl Ctl for DemoCtl {
    fn size(&mut self) -> (f64, f64) {
        let s = self.0.lock().unwrap();
        (s.screen_w as f64, s.screen_h as f64)
    }
    fn location(&mut self) -> Option<(f64, f64)> { Some(self.0.lock().unwrap().cursor) }
    fn move_to(&mut self, x: f64, y: f64) { self.0.lock().unwrap().cursor = (x, y); }
    fn button(&mut self, b: &str, down: bool) { self.log(format!("mouse {b} {}", if down { "down" } else { "up" })); }
    fn scroll(&mut self, dx: i32, dy: i32) { self.log(format!("scroll {dx},{dy}")); }
    fn key_tap(&mut self, key: &str, m: &[String]) {
        let mut parts: Vec<&str> = m.iter().map(String::as_str).collect();
        parts.push(key);
        self.log(format!("key {}", parts.join("+")));
    }
    fn key_down(&mut self, key: &str) { self.log(format!("key down {key}")); }
    fn key_up(&mut self, key: &str) { self.log(format!("key up {key}")); }
    fn text(&mut self, text: &str) { self.log(format!("text {text:?}")); }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn keys() {
        assert_eq!(map_key("ArrowUp"), Some(Key::UpArrow));
        assert_eq!(map_key("win"), Some(Key::Meta));
        assert_eq!(map_key("c"), Some(Key::Unicode('c')));
        assert_eq!(map_key("F12"), Some(Key::F12));
        assert_eq!(map_key("Unidentified"), None);
    }
}
