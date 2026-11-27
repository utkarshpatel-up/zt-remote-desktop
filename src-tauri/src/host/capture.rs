//! Screen capture -> JPEG. A still screen produces no frames at all.

use super::DemoState;
use image::RgbaImage;
use std::sync::{Arc, Mutex};

pub trait Capture {
    /// A JPEG, or None when the picture and settings are unchanged since the last call.
    fn frame(&mut self, quality: u8, max_width: u32) -> Result<Option<Vec<u8>>, String>;
}

fn encode(pixels: &[u8], w: u32, h: u32, color: jpeg_encoder::ColorType, quality: u8)
    -> Result<Vec<u8>, String>
{
    let mut out = Vec::with_capacity((w * h / 8) as usize);
    jpeg_encoder::Encoder::new(&mut out, quality)
        .encode(pixels, w as u16, h as u16, color)
        .map_err(|e| e.to_string())?;
    Ok(out)
}

/// The primary display, via xcap.
pub struct RealCapture {
    monitor: xcap::Monitor,
    last: Option<(u8, u32, RgbaImage)>,
}

impl RealCapture {
    pub fn new() -> Result<Self, String> {
        let all = xcap::Monitor::all().map_err(|e| format!("no screen found: {e}"))?;
        let primary = all.iter().position(|m| m.is_primary().unwrap_or(false)).unwrap_or(0);
        let monitor = all.into_iter().nth(primary).ok_or("no screen found")?;
        Ok(RealCapture { monitor, last: None })
    }
}

impl Capture for RealCapture {
    fn frame(&mut self, quality: u8, max_width: u32) -> Result<Option<Vec<u8>>, String> {
        let img = self.monitor.capture_image().map_err(|e| format!("screen capture failed: {e}"))?;
        if let Some((q, mw, prev)) = &self.last {
            if *q == quality && *mw == max_width && prev.dimensions() == img.dimensions()
                && prev.as_raw() == img.as_raw()
            {
                return Ok(None);
            }
        }
        let (w, h) = img.dimensions();
        let jpeg = if max_width > 0 && w > max_width {
            let nh = ((h as u64 * max_width as u64) / w as u64).max(1) as u32;
            let small = image::imageops::thumbnail(&img, max_width, nh);
            encode(small.as_raw(), max_width, nh, jpeg_encoder::ColorType::Rgba, quality)?
        } else {
            encode(img.as_raw(), w, h, jpeg_encoder::ColorType::Rgba, quality)?
        };
        self.last = Some((quality, max_width, img));
        Ok(Some(jpeg))
    }
}

/// Animated test pattern so the whole pipeline can be tested anywhere.
pub struct DemoCapture {
    pub state: Arc<Mutex<DemoState>>,
    sent: bool,
}

impl DemoCapture {
    pub fn new(state: Arc<Mutex<DemoState>>) -> Self { DemoCapture { state, sent: false } }
}

impl Capture for DemoCapture {
    fn frame(&mut self, quality: u8, _max_width: u32) -> Result<Option<Vec<u8>>, String> {
        let (n, cursor, still, sw, sh) = {
            let mut s = self.state.lock().unwrap();
            s.frames += 1;
            (s.frames, s.cursor, s.still, s.screen_w, s.screen_h)
        };
        if still && self.sent {
            return Ok(None);
        }
        self.sent = true;
        let (w, h) = (960u32, 540u32);
        let mut px = vec![0u8; (w * h * 3) as usize];
        let mut fill = |x0: u32, y0: u32, x1: u32, y1: u32, c: [u8; 3]| {
            for y in y0..y1.min(h) {
                for x in x0..x1.min(w) {
                    let i = ((y * w + x) * 3) as usize;
                    px[i..i + 3].copy_from_slice(&c);
                }
            }
        };
        fill(0, 0, w, h, [24, 28, 38]);
        fill(0, 0, w, 46, [16, 18, 24]);
        let x = 60 + ((n * 7) % 780) as u32;
        fill(x, 200, x + 120, 320, [64, 160, 255]);
        let cx = (cursor.0 * w as f64 / sw as f64) as u32;
        let cy = (cursor.1 * h as f64 / sh as f64) as u32;
        fill(cx.saturating_sub(6), cy.saturating_sub(6), cx + 6, cy + 6, [255, 210, 60]);
        encode(&px, w, h, jpeg_encoder::ColorType::Rgb, quality).map(Some)
    }
}
