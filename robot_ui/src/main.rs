// robot_ui — D435i 機器人視覺 Rust 前端 (egui)
//
// 「任務控制 / HUD」風格：近黑底 + 青色磷光主色 + 琥珀副色，Consolas 技術字型，
// 競技場做成雷達式 HUD。連到 Python 後端 (vision_server.py) 接收影像與狀態。
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use eframe::egui;
use egui::{Color32, FontId, Margin, Pos2, Rect, RichText, Rounding, Stroke};
use serde::Deserialize;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

const HOST: &str = "127.0.0.1";
const PORT: u16 = 50505;
const HOLD_SEC: f64 = 0.2;
const ARENA_W: f32 = 900.0;
const ARENA_H: f32 = 560.0;
const ROBOT_PX: f32 = 90.0;
const OBST_MIN: f32 = 0.3; // 與後端一致
const OBST_MAX: f32 = 4.0;
const FALL_TRIGGER_SEC: f64 = 1.5; // 持續跌倒幾秒才警報

const ROBOT_SVG: &[u8] = include_bytes!("../assets/robot.svg");

// ---------- 調色盤 (HUD / 任務控制) ----------
const C_BG: Color32 = Color32::from_rgb(8, 11, 16);
const C_PANEL: Color32 = Color32::from_rgb(16, 21, 29);
const C_PANEL2: Color32 = Color32::from_rgb(23, 30, 40);
const C_BORDER: Color32 = Color32::from_rgb(34, 46, 61);
const C_ACCENT: Color32 = Color32::from_rgb(64, 214, 255); // 青色磷光
const C_AMBER: Color32 = Color32::from_rgb(255, 200, 74); // 琥珀
const C_TEXT: Color32 = Color32::from_rgb(206, 218, 228);
const C_MUTED: Color32 = Color32::from_rgb(108, 124, 142);
const C_OK: Color32 = Color32::from_rgb(74, 222, 128);
const C_WARN: Color32 = Color32::from_rgb(255, 105, 97);

#[derive(Clone, Default, Deserialize)]
struct Status {
    #[serde(default)]
    person: bool,
    #[serde(default)]
    dist: f32,
    #[serde(default)]
    fingers: i32,
    #[serde(default)]
    gesture: String,
    #[serde(default)]
    finger_states: Vec<bool>,
    #[serde(default)]
    obstacles: Vec<f32>,
    #[serde(default)]
    fallen: bool,
    #[serde(default)]
    tilt: f32,
    #[serde(default)]
    roll: f32,
    #[serde(default)]
    measure: Option<Measure>,
    #[serde(default)]
    picked: bool,
    #[serde(default)]
    has_template: bool,
    #[serde(default)]
    n_views: i32,
    #[serde(default)]
    matched: bool,
    #[serde(default)]
    match_via: String,
    #[serde(default)]
    msg: String,
}

#[derive(Clone, Default, Deserialize)]
struct Measure {
    #[serde(default)]
    length: f32, // 長(公尺) — 最長主軸
    #[serde(default)]
    width: f32, // 寬(公尺) — 次長主軸
    #[serde(default)]
    height: f32, // 高/厚(公尺) — 最短主軸
    #[serde(default)]
    tilt: f32, // 水平傾斜(度)
    #[serde(default)]
    dist: f32, // 距離鏡頭(公尺)
    #[serde(default)]
    n_pts: i32, // 參與量測的點雲點數
}

struct FrameMsg {
    status: Status,
    image: egui::ColorImage,
}

// ---------- 網路 ----------
fn read_u32(s: &mut impl Read) -> std::io::Result<u32> {
    let mut b = [0u8; 4];
    s.read_exact(&mut b)?;
    Ok(u32::from_be_bytes(b))
}
fn read_n(s: &mut impl Read, n: usize) -> std::io::Result<Vec<u8>> {
    let mut v = vec![0u8; n];
    s.read_exact(&mut v)?;
    Ok(v)
}

fn net_thread(
    ctx: egui::Context,
    tx: Sender<FrameMsg>,
    writer: Arc<Mutex<Option<TcpStream>>>,
    connected: Arc<AtomicBool>,
) {
    loop {
        let stream = match TcpStream::connect((HOST, PORT)) {
            Ok(s) => s,
            Err(_) => {
                thread::sleep(Duration::from_millis(500));
                continue;
            }
        };
        stream.set_nodelay(true).ok();
        if let Ok(wc) = stream.try_clone() {
            *writer.lock().unwrap() = Some(wc);
        }
        connected.store(true, Ordering::SeqCst);
        ctx.request_repaint();

        let mut rd = stream;
        loop {
            let json_len = match read_u32(&mut rd) {
                Ok(v) => v as usize,
                Err(_) => break,
            };
            let jbytes = match read_n(&mut rd, json_len) {
                Ok(v) => v,
                Err(_) => break,
            };
            let jpeg_len = match read_u32(&mut rd) {
                Ok(v) => v as usize,
                Err(_) => break,
            };
            let jpeg = match read_n(&mut rd, jpeg_len) {
                Ok(v) => v,
                Err(_) => break,
            };
            let status: Status = serde_json::from_slice(&jbytes).unwrap_or_default();
            let dynimg = match image::load_from_memory(&jpeg) {
                Ok(i) => i.to_rgba8(),
                Err(_) => continue,
            };
            let size = [dynimg.width() as usize, dynimg.height() as usize];
            let image = egui::ColorImage::from_rgba_unmultiplied(size, &dynimg.into_raw());
            if tx.send(FrameMsg { status, image }).is_err() {
                return;
            }
            ctx.request_repaint();
        }

        connected.store(false, Ordering::SeqCst);
        *writer.lock().unwrap() = None;
        ctx.request_repaint();
        thread::sleep(Duration::from_millis(500));
    }
}

// ---------- 繪圖小工具 ----------
fn fill_gradient(p: &egui::Painter, rect: Rect, top: Color32, bottom: Color32) {
    use egui::epaint::{Vertex, WHITE_UV};
    let mut mesh = egui::Mesh::default();
    let mut v = |x, y, c| {
        mesh.vertices.push(Vertex {
            pos: egui::pos2(x, y),
            uv: WHITE_UV,
            color: c,
        });
    };
    v(rect.left(), rect.top(), top);
    v(rect.right(), rect.top(), top);
    v(rect.right(), rect.bottom(), bottom);
    v(rect.left(), rect.bottom(), bottom);
    mesh.indices.extend_from_slice(&[0, 1, 2, 0, 2, 3]);
    p.add(egui::Shape::mesh(mesh));
}

#[allow(dead_code)]
fn fill_tri(p: &egui::Painter, a: Pos2, b: Pos2, c: Pos2, color: Color32) {
    use egui::epaint::{Vertex, WHITE_UV};
    let mut m = egui::Mesh::default();
    for pt in [a, b, c] {
        m.vertices.push(Vertex {
            pos: pt,
            uv: WHITE_UV,
            color,
        });
    }
    m.indices.extend_from_slice(&[0, 1, 2]);
    p.add(egui::Shape::mesh(m));
}

fn glow(p: &egui::Painter, center: Pos2, r: f32, rgb: (u8, u8, u8)) {
    for i in 0..7 {
        let f = i as f32 / 7.0;
        let a = (28.0 * (1.0 - f)) as u8;
        p.circle_filled(
            center,
            r * (0.5 + f * 1.2),
            Color32::from_rgba_unmultiplied(rgb.0, rgb.1, rgb.2, a),
        );
    }
}

fn corner_ticks(p: &egui::Painter, rect: Rect, len: f32, col: Color32) {
    let s = Stroke::new(1.5, col);
    let (l, r, t, b) = (rect.left(), rect.right(), rect.top(), rect.bottom());
    for (cx, cy, dx, dy) in [
        (l, t, 1.0, 1.0),
        (r, t, -1.0, 1.0),
        (l, b, 1.0, -1.0),
        (r, b, -1.0, -1.0),
    ] {
        p.line_segment([egui::pos2(cx, cy), egui::pos2(cx + dx * len, cy)], s);
        p.line_segment([egui::pos2(cx, cy), egui::pos2(cx, cy + dy * len)], s);
    }
}

// ---------- 模擬機器人 ----------
/// 動作字串 → (前進分量, 轉向分量)。前進: +1前/-1後/0不動；轉向: -1左/+1右/0直行。
fn action_drive_turn(a: &str) -> (f32, f32) {
    match a {
        "FORWARD" => (1.0, 0.0),
        "BACK" => (-1.0, 0.0),
        "FORWARD_LEFT" => (1.0, -1.0),
        "FORWARD_RIGHT" => (1.0, 1.0),
        "BACK_LEFT" => (-1.0, -1.0),
        "BACK_RIGHT" => (-1.0, 1.0),
        "LEFT" => (0.0, -1.0),
        "RIGHT" => (0.0, 1.0),
        _ => (0.0, 0.0), // STOP
    }
}

struct RobotSim {
    x: f32,
    y: f32,
    angle: f32,
    speed: f32,
    turn: f32,
    action: String,
    avoiding: bool,
    stuck: f32,        // 無法前進的累積(卡住偵測)
    escape_timer: f32, // >0 時執行脫困倒車
    escape_dir: f32,   // 脫困轉向
    trail: Vec<(f32, f32)>,
    w: f32,
    h: f32,
}

impl RobotSim {
    fn new(w: f32, h: f32) -> Self {
        let mut r = Self {
            x: 0.0,
            y: 0.0,
            angle: 0.0,
            speed: 1.2,
            turn: 0.04,
            action: "STOP".into(),
            avoiding: false,
            stuck: 0.0,
            escape_timer: 0.0,
            escape_dir: 1.0,
            trail: Vec::new(),
            w,
            h,
        };
        r.reset();
        r
    }
    fn reset(&mut self) {
        self.x = self.w / 2.0;
        self.y = self.h / 2.0;
        self.angle = -std::f32::consts::FRAC_PI_2;
        self.stuck = 0.0;
        self.escape_timer = 0.0;
        self.trail.clear();
    }

    fn clamp_pos(&mut self) {
        let m = ROBOT_PX / 2.0;
        self.x = self.x.clamp(m, self.w - m);
        self.y = self.y.clamp(m, self.h - m);
    }

    /// 沿 heading 方向的「淨空距離」：錐形範圍內最近障礙(含牆)；無=max_look
    fn ray_clearance(&self, obstacles: &[(f32, f32)], heading: f32, max_look: f32) -> f32 {
        use std::f32::consts::PI;
        let mut m = max_look;
        // 障礙物
        for &(ox, oy) in obstacles {
            let (dx, dy) = (ox - self.x, oy - self.y);
            let d = (dx * dx + dy * dy).sqrt();
            if d < 1.0 {
                return 0.0;
            }
            let mut da = dy.atan2(dx) - heading;
            while da > PI {
                da -= 2.0 * PI;
            }
            while da < -PI {
                da += 2.0 * PI;
            }
            if da.abs() < 0.5 && d < m {
                m = d;
            }
        }
        // 牆壁：往 heading 走會撞到哪一邊
        let mar = ROBOT_PX / 2.0;
        let (cx, sy) = (heading.cos(), heading.sin());
        if cx.abs() > 1e-3 {
            let wx = if cx > 0.0 { self.w - mar - self.x } else { self.x - mar };
            m = m.min((wx / cx.abs()).max(0.0));
        }
        if sy.abs() > 1e-3 {
            let wy = if sy > 0.0 { self.h - mar - self.y } else { self.y - mar };
            m = m.min((wy / sy.abs()).max(0.0));
        }
        m
    }
    fn step(&mut self, obstacles: &[(f32, f32)], avoid: bool) {
        use std::f32::consts::PI;
        self.avoiding = false;

        if self.action == "RESET" {
            self.reset();
            return;
        }
        // 動作拆成「前進分量 + 轉向分量」(可同時 → 前進/後退時邊走邊轉成弧線)
        let (drive, turn) = action_drive_turn(&self.action);
        self.angle += turn * self.turn;          // 先套用轉向
        if drive == 0.0 {
            // 純轉向 / 停止：不前進，清掉避障狀態
            self.stuck = 0.0;
            self.escape_timer = 0.0;
            return;
        }

        let back = drive < 0.0;

        // 避障關閉：直接前進/後退(僅牆壁夾住)
        if !avoid {
            let sgn = if back { -1.0 } else { 1.0 };
            self.x += sgn * self.speed * self.angle.cos();
            self.y += sgn * self.speed * self.angle.sin();
            self.clamp_pos();
            self.trail.push((self.x, self.y));
            if self.trail.len() > 160 {
                self.trail.remove(0);
            }
            return;
        }

        let desired = if back { self.angle + PI } else { self.angle };
        let max_look = 240.0;
        let avoid_zone = ROBOT_PX * 0.95;

        // 脫困倒車：卡住時短暫倒退並大轉向
        if self.escape_timer > 0.0 {
            self.escape_timer -= 1.0;
            self.avoiding = true;
            self.x -= 1.6 * desired.cos();
            self.y -= 1.6 * desired.sin();
            self.angle += self.escape_dir * self.turn * 1.5;
            self.clamp_pos();
            return;
        }

        // gap-seeking：在 desired 附近掃描候選方向，挑「淨空大又最接近目標」的
        let mut best = desired;
        let mut best_score = f32::MIN;
        let mut k = -7;
        while k <= 7 {
            let cand = desired + k as f32 * 0.16; // ±~64°，9° 一格
            let clr = self.ray_clearance(obstacles, cand, max_look);
            let score = clr - (cand - desired).abs() * 55.0; // 偏離目標方向要扣分
            if score > best_score {
                best_score = score;
                best = cand;
            }
            k += 1;
        }

        // 平滑轉向到最佳方向
        let mut da = best - self.angle;
        while da > PI {
            da -= 2.0 * PI;
        }
        while da < -PI {
            da += 2.0 * PI;
        }
        self.angle += da.clamp(-self.turn * 2.2, self.turn * 2.2);
        let deviating = (best - desired).abs() > 0.1;

        // 依目前實際方向的淨空決定速度
        let clr = self.ray_clearance(obstacles, self.angle, max_look);
        if clr <= avoid_zone {
            // 太近：只轉不前進，累積卡住
            self.avoiding = true;
            self.stuck += 1.0;
        } else {
            let speed =
                self.speed * ((clr - avoid_zone) / (max_look - avoid_zone)).clamp(0.25, 1.0);
            let sgn = if back { -1.0 } else { 1.0 };
            self.x += sgn * speed * self.angle.cos();
            self.y += sgn * speed * self.angle.sin();
            self.stuck = (self.stuck - 1.5).max(0.0);
            self.avoiding = deviating || clr < max_look * 0.75;
            self.trail.push((self.x, self.y));
            if self.trail.len() > 160 {
                self.trail.remove(0);
            }
        }
        self.clamp_pos();

        // 卡住夠久 → 啟動脫困
        if self.stuck > 42.0 {
            self.escape_timer = 36.0;
            // 往較空曠那側脫困
            let left = self.ray_clearance(obstacles, self.angle - 1.2, max_look);
            let right = self.ray_clearance(obstacles, self.angle + 1.2, max_look);
            self.escape_dir = if right >= left { 1.0 } else { -1.0 };
            self.stuck = 0.0;
        }
    }

    fn draw(
        &self,
        p: &egui::Painter,
        rect: Rect,
        tex: Option<&egui::TextureHandle>,
        t: f32,
        obstacles: &[(f32, f32)],
        avoid: bool,
    ) {
        use egui::{pos2, Align2};
        // 背景漸層 + 邊框
        fill_gradient(p, rect, Color32::from_rgb(14, 19, 27), Color32::from_rgb(6, 9, 13));
        p.rect_stroke(rect, Rounding::same(8.0), Stroke::new(1.0, C_BORDER));

        // 網格（中央亮、邊緣淡）
        let center = rect.center();
        let grid = |p: &egui::Painter, a: Rect| {
            let step = 44.0;
            let gcol = Color32::from_rgba_unmultiplied(64, 214, 255, 14);
            let mut gx = a.left() + ((center.x - a.left()) % step);
            while gx < a.right() {
                p.line_segment([pos2(gx, a.top()), pos2(gx, a.bottom())], Stroke::new(1.0, gcol));
                gx += step;
            }
            let mut gy = a.top() + ((center.y - a.top()) % step);
            while gy < a.bottom() {
                p.line_segment([pos2(a.left(), gy), pos2(a.right(), gy)], Stroke::new(1.0, gcol));
                gy += step;
            }
        };
        grid(p, rect);

        // 測距環（雷達感）
        for k in 1..=4 {
            let r = k as f32 * 70.0;
            p.circle_stroke(
                center,
                r,
                Stroke::new(1.0, Color32::from_rgba_unmultiplied(64, 214, 255, 16)),
            );
        }
        // 中央十字準星
        let rl = 12.0;
        let cr = Stroke::new(1.0, Color32::from_rgba_unmultiplied(64, 214, 255, 60));
        p.line_segment([center - egui::vec2(rl, 0.0), center + egui::vec2(rl, 0.0)], cr);
        p.line_segment([center - egui::vec2(0.0, rl), center + egui::vec2(0.0, rl)], cr);

        corner_ticks(p, rect.shrink(6.0), 16.0, C_ACCENT);

        let off = rect.min.to_vec2();

        // 深度障礙物（俯視，僅避障開啟時顯示）：近=紅、遠=青
        if avoid {
            for &(ox, oy) in obstacles {
                let sp = pos2(ox, oy) + off;
                let near = (oy / self.h).clamp(0.0, 1.0); // 越靠下越近
                let r = (255.0 * near + 64.0 * (1.0 - near)) as u8;
                let g = (105.0 * near + 214.0 * (1.0 - near)) as u8;
                let b = (97.0 * near + 255.0 * (1.0 - near)) as u8;
                glow(p, sp, 9.0 + 8.0 * near, (r, g, b));
                p.circle_filled(sp, 4.5, Color32::from_rgb(r, g, b));
            }
        }

        // 軌跡（青色漸隱）
        let n = self.trail.len().max(1) as f32;
        for (i, (tx, ty)) in self.trail.iter().enumerate() {
            let a = (i as f32 / n) * 0.7 + 0.1;
            p.circle_filled(
                pos2(*tx, *ty) + off,
                2.2,
                Color32::from_rgba_unmultiplied(64, 214, 255, (a * 160.0) as u8),
            );
        }

        let rc = pos2(self.x, self.y) + off;
        // 前方雷達掃描扇形(僅避障開啟時顯示)：射線長度=該方向淨空，近=紅、空=青
        if avoid {
            let max_look = 240.0;
            let rays = 15;
            for j in 0..rays {
                let frac = j as f32 / (rays - 1) as f32;
                let ang = self.angle - 1.05 + frac * 2.1; // ±~60°
                let clr = self.ray_clearance(obstacles, ang, max_look);
                let end = rc + egui::vec2(ang.cos(), ang.sin()) * clr;
                let s = (clr / max_look).clamp(0.0, 1.0); // 1=空曠
                let col = Color32::from_rgba_unmultiplied(
                    (255.0 * (1.0 - s) + 64.0 * s) as u8,
                    (90.0 * (1.0 - s) + 214.0 * s) as u8,
                    (90.0 * (1.0 - s) + 255.0 * s) as u8,
                    70,
                );
                p.line_segment([rc, end], Stroke::new(1.5, col));
                if clr < max_look {
                    p.circle_filled(end, 2.0, col);
                }
            }
        }
        // 機器人下方輝光（避障時轉琥珀並加速脈動）
        let pulse = 0.5 + 0.5 * (t * if self.avoiding { 8.0 } else { 3.5 }).sin();
        let glow_rgb = if self.avoiding {
            (255, 170, 60)
        } else {
            (64, 214, 255)
        };
        glow(p, rc, ROBOT_PX * (0.42 + 0.10 * pulse), glow_rgb);

        let rot = self.angle + std::f32::consts::FRAC_PI_2;
        if let Some(tex) = tex {
            let mut mesh = egui::Mesh::with_texture(tex.id());
            let half = ROBOT_PX / 2.0;
            let corners = [(-half, -half), (half, -half), (half, half), (-half, half)];
            let uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)];
            let (sin, cos) = rot.sin_cos();
            for i in 0..4 {
                let (cx, cy) = corners[i];
                mesh.vertices.push(egui::epaint::Vertex {
                    pos: pos2(rc.x + cx * cos - cy * sin, rc.y + cx * sin + cy * cos),
                    uv: pos2(uvs[i].0, uvs[i].1),
                    color: Color32::WHITE,
                });
            }
            mesh.indices.extend_from_slice(&[0, 1, 2, 0, 2, 3]);
            p.add(egui::Shape::mesh(mesh));
        } else {
            p.circle_filled(rc, ROBOT_PX / 2.5, C_OK);
        }

        // 動作標籤（避障時顯示警示）
        let (label, lcol) = if self.avoiding {
            ("⚠ 避障中", C_AMBER)
        } else {
            let l = match self.action.as_str() {
                "FORWARD" => "前進",
                "BACK" => "後退",
                "LEFT" => "左轉",
                "RIGHT" => "右轉",
                "FORWARD_LEFT" => "前進左轉",
                "FORWARD_RIGHT" => "前進右轉",
                "BACK_LEFT" => "後退左轉",
                "BACK_RIGHT" => "後退右轉",
                "RESET" => "歸位",
                _ => "停止",
            };
            (l, Color32::from_rgb(180, 232, 210))
        };
        p.text(
            rc + egui::vec2(0.0, ROBOT_PX / 2.0 + 14.0),
            Align2::CENTER_CENTER,
            label,
            FontId::proportional(15.0),
            lcol,
        );

        // 左上角遙測讀數
        let hdg = (self.angle.to_degrees() + 360.0) % 360.0;
        p.text(
            rect.left_top() + egui::vec2(14.0, 12.0),
            Align2::LEFT_TOP,
            format!("POS {:>4.0},{:>4.0}   HDG {:>3.0}°", self.x, self.y, hdg),
            FontId::monospace(13.0),
            C_MUTED,
        );
        // 右上角 SIM + 閃爍點
        let blink = ((t * 2.0).sin() > 0.0) as u8;
        p.text(
            rect.right_top() + egui::vec2(-16.0, 12.0),
            Align2::RIGHT_TOP,
            "● SIM",
            FontId::monospace(13.0),
            if blink == 1 { C_AMBER } else { C_MUTED },
        );
    }
}

// ---------- App ----------
struct App {
    frame_rx: Receiver<FrameMsg>,
    writer: Arc<Mutex<Option<TcpStream>>>,
    connected: Arc<AtomicBool>,
    texture: Option<egui::TextureHandle>,
    robot_tex: Option<egui::TextureHandle>,
    status: Status,
    pose: bool,
    hands: bool,
    depth: bool,
    flip: bool,
    dfilter: bool, // 深度濾鏡(去噪/補洞)
    measure: bool, // 物體量測模式
    detect: bool,  // 自動辨識已教學物體
    zoom: f32,     // 畫面縮放倍率
    disp: f32,     // 正方形畫布邊長(px)
    robot: RobotSim,
    start: Instant,
    cmd_action: String,
    cmd_desc: String,
    cmd_until: f64,
    drive: String, // 目前直行方向(FORWARD/BACK/STOP)，給手勢2/3 組合用
    log: String,
    sized: bool,      // 競技場第一次拿到實際大小後置中機器人
    show_arena: bool, // 競技場可收合
    avoid_on: bool,   // 避障開關(預設關)
    level_on: bool,   // IMU 自動水平(預設開)
    roll_smooth: f32, // 平滑後的 roll(度)
    // 跌倒警報狀態機
    fall_since: Option<f64>,
    fall_alarm: bool,
    fall_armed: bool,
    fall_count: u32, // 警報觸發次數
}

impl App {
    fn new(cc: &eframe::CreationContext<'_>) -> Self {
        setup_fonts(&cc.egui_ctx);
        setup_theme(&cc.egui_ctx);
        let robot_tex = rasterize_svg(ROBOT_SVG, 256)
            .map(|img| cc.egui_ctx.load_texture("robot", img, egui::TextureOptions::LINEAR));

        let (tx, rx) = mpsc::channel();
        let writer = Arc::new(Mutex::new(None));
        let connected = Arc::new(AtomicBool::new(false));
        {
            let ctx = cc.egui_ctx.clone();
            let w = writer.clone();
            let c = connected.clone();
            thread::spawn(move || net_thread(ctx, tx, w, c));
        }
        Self {
            frame_rx: rx,
            writer,
            connected,
            texture: None,
            robot_tex,
            status: Status::default(),
            pose: false,
            hands: false,
            depth: false,
            flip: true,
            dfilter: true,
            measure: false,
            detect: false,
            zoom: 1.0,
            disp: 800.0,
            robot: RobotSim::new(ARENA_W, ARENA_H),
            start: Instant::now(),
            cmd_action: "STOP".into(),
            cmd_desc: "停止".into(),
            cmd_until: 0.0,
            drive: "STOP".into(),
            log: String::new(),
            sized: false,
            show_arena: true,
            avoid_on: false,
            level_on: true,
            roll_smooth: 0.0,
            fall_since: None,
            fall_alarm: false,
            fall_armed: true,
            fall_count: 0,
        }
    }

    fn send_json(&self, v: serde_json::Value) {
        if let Some(s) = self.writer.lock().unwrap().as_mut() {
            let _ = writeln!(s, "{}", v);
            let _ = s.flush();
        }
    }
    fn send_cfg(&self) {
        self.send_json(serde_json::json!({
            "pose": self.pose, "hands": self.hands,
            "depth": self.depth, "flip": self.flip,
            "measure": self.measure, "level": self.level_on,
            "detect": self.detect, "dfilter": self.dfilter,
            "zoom": self.zoom, "disp": self.disp as i32,
        }));
    }
    /// 手勢 → 動作。手勢 2/3 會「維持目前前進/後退」並加上左/右轉。
    fn finger_action(&mut self, f: i32) -> (String, String) {
        match f.min(5) {
            1 => {
                self.drive = "FORWARD".into();
                ("FORWARD".into(), "前進".into())
            }
            4 => {
                self.drive = "BACK".into();
                ("BACK".into(), "後退".into())
            }
            5 => {
                self.drive = "STOP".into();
                ("RESET".into(), "歸位".into())
            }
            2 => match self.drive.as_str() {
                "FORWARD" => ("FORWARD_LEFT".into(), "前進左轉".into()),
                "BACK" => ("BACK_LEFT".into(), "後退左轉".into()),
                _ => ("LEFT".into(), "左轉".into()),
            },
            3 => match self.drive.as_str() {
                "FORWARD" => ("FORWARD_RIGHT".into(), "前進右轉".into()),
                "BACK" => ("BACK_RIGHT".into(), "後退右轉".into()),
                _ => ("RIGHT".into(), "右轉".into()),
            },
            _ => {
                self.drive = "STOP".into();
                ("STOP".into(), "停止".into())
            }
        }
    }
    fn read_keys(&self, ctx: &egui::Context) -> Option<&'static str> {
        ctx.input(|i| {
            if i.key_down(egui::Key::ArrowUp) {
                Some("FORWARD")
            } else if i.key_down(egui::Key::ArrowDown) {
                Some("BACK")
            } else if i.key_down(egui::Key::ArrowLeft) {
                Some("LEFT")
            } else if i.key_down(egui::Key::ArrowRight) {
                Some("RIGHT")
            } else if i.key_down(egui::Key::Space) {
                Some("STOP")
            } else if i.key_down(egui::Key::R) {
                Some("RESET")
            } else {
                None
            }
        })
    }
}

// 框起來的卡片
fn card(ui: &mut egui::Ui, accent: Color32, add: impl FnOnce(&mut egui::Ui)) {
    egui::Frame::none()
        .fill(C_PANEL)
        .stroke(Stroke::new(1.0, accent))
        .rounding(Rounding::same(8.0))
        .inner_margin(Margin::same(11.0))
        .show(ui, |ui| {
            ui.set_width(ui.available_width());
            add(ui);
        });
}

/// 在 rect 內以 cover 方式畫相機貼圖，並旋轉 angle(弧度) 使畫面水平。
fn draw_cam_rotated(p: &egui::Painter, rect: Rect, tex: &egui::TextureHandle, angle: f32) {
    let ts = tex.size_vec2();
    let sc0 = (rect.width() / ts.x).max(rect.height() / ts.y);
    let sc = sc0 * (angle.cos().abs() + angle.sin().abs()); // 放大蓋住旋轉後的空角
    let (hw, hh) = (ts.x * sc / 2.0, ts.y * sc / 2.0);
    let c = rect.center();
    let (s, co) = angle.sin_cos();
    let corners = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)];
    let uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)];
    let mut m = egui::Mesh::with_texture(tex.id());
    for i in 0..4 {
        let (cx, cy) = corners[i];
        m.vertices.push(egui::epaint::Vertex {
            pos: egui::pos2(c.x + cx * co - cy * s, c.y + cx * s + cy * co),
            uv: egui::pos2(uvs[i].0, uvs[i].1),
            color: Color32::WHITE,
        });
    }
    m.indices.extend_from_slice(&[0, 1, 2, 0, 2, 3]);
    p.add(egui::Shape::mesh(m));
}

fn label_mono(ui: &mut egui::Ui, txt: &str) {
    ui.label(
        RichText::new(txt)
            .font(FontId::monospace(11.0))
            .color(C_MUTED),
    );
}

/// 把後端「每個水平方向的最近障礙距離」轉成競技場座標(俯視)：
/// 左右 = 方向，上下 = 距離(近在底、遠在頂)。
fn obstacle_points(obstacles: &[f32], w: f32, h: f32) -> Vec<(f32, f32)> {
    let cols = obstacles.len();
    if cols == 0 {
        return Vec::new();
    }
    let mut pts = Vec::with_capacity(cols);
    for (i, &d) in obstacles.iter().enumerate() {
        if d <= 0.0 {
            continue;
        }
        let x = (i as f32 + 0.5) / cols as f32 * w;
        let frac = ((d - OBST_MIN) / (OBST_MAX - OBST_MIN)).clamp(0.0, 1.0); // 0近 1遠
        let y = h * (0.92 - 0.84 * frac); // 近→底部、遠→頂部
        pts.push((x, y));
    }
    pts
}

impl eframe::App for App {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        while let Ok(f) = self.frame_rx.try_recv() {
            if !f.status.msg.is_empty() {
                self.log = f.status.msg.clone();
            }
            self.status = f.status;
            let opts = egui::TextureOptions::LINEAR;
            match &mut self.texture {
                Some(t) => t.set(f.image, opts),
                None => self.texture = Some(ctx.load_texture("video", f.image, opts)),
            }
        }

        // IMU roll 平滑(EMA)，避免抖動
        self.roll_smooth += (self.status.roll - self.roll_smooth) * 0.15;

        let t = self.start.elapsed().as_secs_f32();
        let now = self.start.elapsed().as_secs_f64();
        let key = self.read_keys(ctx);
        let (act_en, desc_disp) = if let Some(a) = key {
            self.cmd_until = 0.0;
            self.robot.action = a.into();
            (a, "鍵盤控制".to_string())
        } else {
            if now >= self.cmd_until {
                let (a, d) = self.finger_action(self.status.fingers);
                self.cmd_action = a;
                self.cmd_desc = d;
                self.cmd_until = now + HOLD_SEC;
            }
            self.robot.action = self.cmd_action.clone();
            let remain = (self.cmd_until - now).max(0.0);
            (
                self.cmd_action.as_str(),
                format!("{} · {:.1}s", self.cmd_desc, remain),
            )
        };
        let act_en = act_en.to_string();
        let connected = self.connected.load(Ordering::SeqCst);

        // ---- 跌倒警報狀態機 ----
        if self.status.fallen {
            if self.fall_since.is_none() {
                self.fall_since = Some(now);
            }
        } else {
            self.fall_since = None;
            self.fall_armed = true; // 站起來 → 重新武裝
        }
        if self.fall_armed {
            if let Some(t0) = self.fall_since {
                if now - t0 >= FALL_TRIGGER_SEC {
                    self.fall_alarm = true;
                    self.fall_armed = false;
                    self.fall_count += 1;
                    // 自動截圖存證(請後端存含骨架的當下畫面)
                    self.send_json(serde_json::json!({"fall_snapshot": true}));
                }
            }
        }

        // ===== 頂部標題列 =====
        egui::TopBottomPanel::top("header")
            .frame(
                egui::Frame::none()
                    .fill(C_PANEL)
                    .inner_margin(Margin::symmetric(16.0, 9.0)),
            )
            .show(ctx, |ui| {
                ui.horizontal(|ui| {
                    ui.label(
                        RichText::new("▶ D435i")
                            .font(FontId::monospace(20.0))
                            .strong()
                            .color(C_ACCENT),
                    );
                    ui.label(
                        RichText::new("DEPTH ROVER CONSOLE")
                            .font(FontId::monospace(13.0))
                            .color(C_MUTED),
                    );
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        let pulse = 0.45 + 0.55 * (t * 3.0).sin().abs();
                        let (txt, col) = if connected {
                            ("LINK ONLINE", C_OK)
                        } else {
                            ("AWAITING BACKEND", C_WARN)
                        };
                        ui.label(
                            RichText::new(txt)
                                .font(FontId::monospace(13.0))
                                .strong()
                                .color(col.gamma_multiply(0.5 + 0.5 * pulse)),
                        );
                    });
                });
            });

        // ===== 右側欄：相機影像 + 遙測 =====
        egui::SidePanel::right("telemetry")
            .resizable(false)
            .exact_width(404.0)
            .frame(
                egui::Frame::none()
                    .fill(C_BG)
                    .inner_margin(Margin::same(12.0)),
            )
            .show(ctx, |ui| {
              egui::ScrollArea::vertical()
                .auto_shrink([false, false])
                .show(ui, |ui| {
                // 相機影像（移到右上、隨欄寬縮放）
                ui.horizontal(|ui| {
                    ui.label(
                        RichText::new("▶ RGB-D FEED")
                            .font(FontId::monospace(13.0))
                            .strong()
                            .color(C_ACCENT),
                    );
                    if self.level_on {
                        ui.label(
                            RichText::new(format!("⊿ 水平 {:+.0}°", self.roll_smooth))
                                .font(FontId::monospace(12.0))
                                .color(C_AMBER),
                        );
                    }
                });
                ui.add_space(3.0);
                card(ui, C_BORDER, |ui| {
                    if let Some(tex) = &self.texture {
                        let ts = tex.size_vec2();
                        let w = ui.available_width();
                        let h = w * ts.y / ts.x;
                        let (resp, painter) =
                            ui.allocate_painter(egui::vec2(w, h), egui::Sense::click());
                        let rect = resp.rect;
                        let clip = painter.with_clip_rect(rect);
                        clip.rect_filled(rect, Rounding::same(4.0), C_PANEL2);
                        // 水平校正已由後端完成(連同量測一起轉正)，前端不再旋轉
                        draw_cam_rotated(&clip, rect, tex, 0.0);
                        corner_ticks(ui.painter(), rect.shrink(3.0), 10.0, C_ACCENT);
                        // 量測模式下：點影像 → 換算成影像像素 → 送後端選取目標
                        if self.measure && resp.clicked() {
                            if let Some(pos) = resp.interact_pointer_pos() {
                                let sc = (rect.width() / ts.x).max(rect.height() / ts.y);
                                let tx = (pos.x - rect.center().x) / sc + ts.x / 2.0;
                                let ty = (pos.y - rect.center().y) / sc + ts.y / 2.0;
                                let color_w = ts.y; // 彩色區為正方形(深度並排時取左半)
                                if tx >= 0.0 && tx < color_w && ty >= 0.0 && ty < ts.y {
                                    self.send_json(
                                        serde_json::json!({"pick": [tx as i32, ty as i32]}),
                                    );
                                }
                            }
                        }
                    } else {
                        ui.label(
                            RichText::new("○ 等待影像…")
                                .color(C_MUTED)
                                .font(FontId::monospace(14.0)),
                        );
                    }
                });

                ui.add_space(8.0);

                // 動作徽章（最醒目）
                let badge_col = if act_en == "STOP" { C_AMBER } else { C_ACCENT };
                egui::Frame::none()
                    .fill(badge_col.gamma_multiply(0.14))
                    .stroke(Stroke::new(1.0, badge_col.gamma_multiply(0.7)))
                    .rounding(Rounding::same(8.0))
                    .inner_margin(Margin::symmetric(12.0, 10.0))
                    .show(ui, |ui| {
                        ui.set_width(ui.available_width());
                        label_mono(ui, "ACTION");
                        ui.label(
                            RichText::new(&desc_disp)
                                .font(FontId::proportional(24.0))
                                .strong()
                                .color(badge_col),
                        );
                    });

                ui.add_space(8.0);

                // 手指數 + 手勢 + 五指 LED
                card(ui, C_BORDER, |ui| {
                    label_mono(ui, "FINGERS");
                    ui.horizontal(|ui| {
                        ui.label(
                            RichText::new(format!("{}", self.status.fingers))
                                .font(FontId::monospace(44.0))
                                .strong()
                                .color(C_ACCENT),
                        );
                        ui.add_space(8.0);
                        ui.vertical(|ui| {
                            ui.add_space(14.0);
                            let g = if self.status.gesture.is_empty() {
                                "—"
                            } else {
                                &self.status.gesture
                            };
                            ui.label(RichText::new(g).size(14.0).color(C_TEXT));
                        });
                    });
                    ui.add_space(4.0);
                    ui.horizontal(|ui| {
                        for (i, n) in ["拇", "食", "中", "無", "小"].iter().enumerate() {
                            let on =
                                self.status.finger_states.get(i).copied().unwrap_or(false);
                            let (bg, fg) = if on {
                                (C_ACCENT, C_BG)
                            } else {
                                (C_PANEL2, C_MUTED)
                            };
                            egui::Frame::none()
                                .fill(bg)
                                .rounding(Rounding::same(4.0))
                                .inner_margin(Margin::symmetric(8.0, 4.0))
                                .show(ui, |ui| {
                                    ui.label(RichText::new(*n).color(fg).size(14.0).strong());
                                });
                        }
                    });
                });

                ui.add_space(8.0);

                // 人 + 距離
                let pcol = if self.status.person { C_OK } else { C_MUTED };
                card(ui, C_BORDER, |ui| {
                    ui.horizontal(|ui| {
                        ui.vertical(|ui| {
                            label_mono(ui, "PRESENCE");
                            ui.label(
                                RichText::new(if self.status.person {
                                    "● 有人"
                                } else {
                                    "○ 無人"
                                })
                                .font(FontId::proportional(18.0))
                                .strong()
                                .color(pcol),
                            );
                        });
                        ui.add_space(20.0);
                        ui.vertical(|ui| {
                            label_mono(ui, "DISTANCE");
                            ui.label(
                                if self.status.person && self.status.dist > 0.0 {
                                    RichText::new(format!("{:.2} m", self.status.dist))
                                        .font(FontId::monospace(24.0))
                                        .strong()
                                        .color(C_AMBER)
                                } else {
                                    RichText::new("--- m")
                                        .font(FontId::monospace(24.0))
                                        .color(C_MUTED)
                                },
                            );
                        });
                    });
                    // 姿態（跌倒偵測）
                    if self.status.person {
                        ui.add_space(2.0);
                        if self.status.fallen {
                            ui.label(
                                RichText::new(format!("姿態: ⚠ 跌倒/躺下 ({:.0}°)", self.status.tilt))
                                    .size(14.0)
                                    .strong()
                                    .color(C_WARN),
                            );
                        } else {
                            ui.label(
                                RichText::new(format!("姿態: 站立 ({:.0}°)", self.status.tilt))
                                    .size(13.0)
                                    .color(C_OK),
                            );
                        }
                    }
                });

                ui.add_space(8.0);

                // 框內物體量測（開啟時顯示）
                if self.measure {
                    let mut clear_pick = false;
                    card(ui, C_ACCENT, |ui| {
                        label_mono(ui, "點選物體 3D 量測");
                        if let Some(m) = &self.status.measure {
                            // 長 / 寬 / 高 三維
                            let dim = |ui: &mut egui::Ui, name: &str, val: f32| {
                                ui.vertical(|ui| {
                                    label_mono(ui, name);
                                    ui.label(
                                        RichText::new(format!("{:.1}", val * 100.0))
                                            .font(FontId::monospace(22.0))
                                            .strong()
                                            .color(C_OK),
                                    );
                                });
                            };
                            ui.horizontal(|ui| {
                                dim(ui, "長 cm", m.length);
                                ui.add_space(14.0);
                                dim(ui, "寬 cm", m.width);
                                ui.add_space(14.0);
                                dim(ui, "高 cm", m.height);
                            });
                            ui.add_space(2.0);
                            ui.horizontal(|ui| {
                                ui.label(
                                    RichText::new(format!("水平傾斜 {:+.0}°", m.tilt))
                                        .size(14.0)
                                        .color(C_AMBER),
                                );
                                ui.add_space(12.0);
                                ui.label(
                                    RichText::new(format!("距離 {:.2} m", m.dist))
                                        .font(FontId::monospace(16.0))
                                        .strong()
                                        .color(C_AMBER),
                                );
                            });
                            ui.label(
                                RichText::new(format!("點雲 {} 點 · 高/厚受單視角遮擋影響較大", m.n_pts))
                                    .size(11.0)
                                    .color(C_MUTED),
                            );
                            ui.add_space(4.0);
                            if ui.button("✖ 清除選取").clicked() {
                                clear_pick = true;
                            }
                        } else {
                            let hint = if self.status.picked {
                                "⌖ 目標遺失，請重新點選物體"
                            } else {
                                "○ 點一下畫面中的物體來量測"
                            };
                            ui.label(RichText::new(hint).size(15.0).color(C_AMBER));
                        }
                    });
                    if clear_pick {
                        self.send_json(serde_json::json!({"clear_pick": true}));
                    }
                    ui.add_space(6.0);

                    // 教學 / 自動辨識
                    let mut do_teach = false;
                    let mut do_clear_tmpl = false;
                    let mut detect_changed = false;
                    card(ui, C_AMBER, |ui| {
                        label_mono(ui, "教學 / 自動辨識");
                        let tt = if self.status.has_template {
                            format!("樣板: ✓ 已教 {} 個視角", self.status.n_views)
                        } else {
                            "樣板: ✗ 尚未教學（先點選物體再教學）".to_string()
                        };
                        ui.label(RichText::new(tt).size(13.0).color(
                            if self.status.has_template { C_OK } else { C_MUTED },
                        ));
                        ui.horizontal(|ui| {
                            if ui.button("➕ 新增視角").clicked() {
                                do_teach = true;
                            }
                            if ui.button("🗑 清除樣板").clicked() {
                                do_clear_tmpl = true;
                            }
                        });
                        ui.label(
                            RichText::new("物體會以不同角度出現 → 各角度都點選+新增視角")
                                .size(11.0)
                                .color(C_MUTED),
                        );
                        detect_changed = ui
                            .checkbox(&mut self.detect, "🔍 自動辨識已教學物體")
                            .changed();
                        if self.detect {
                            let (txt, col) = if self.status.matched {
                                (
                                    format!("✓ 已辨識 ({})", self.status.match_via),
                                    C_OK,
                                )
                            } else {
                                ("🔍 搜尋中… 物體不在畫面中".to_string(), C_AMBER)
                            };
                            ui.label(
                                RichText::new(txt).size(14.0).strong().color(col),
                            );
                        }
                    });
                    if do_teach {
                        self.send_json(serde_json::json!({"teach": true}));
                    }
                    if do_clear_tmpl {
                        self.detect = false;
                        self.send_json(serde_json::json!({"clear_template": true}));
                    }
                    if detect_changed {
                        self.send_cfg();
                    }
                    ui.add_space(8.0);
                }

                // 障礙雷達（僅避障開啟時顯示）
                if self.avoid_on {
                let nearest = self
                    .status
                    .obstacles
                    .iter()
                    .cloned()
                    .filter(|d| *d > 0.0)
                    .fold(f32::INFINITY, f32::min);
                let obst_cnt = self.status.obstacles.iter().filter(|d| **d > 0.0).count();
                card(ui, C_BORDER, |ui| {
                    ui.horizontal(|ui| {
                        ui.vertical(|ui| {
                            label_mono(ui, "NEAREST OBSTACLE");
                            ui.label(
                                if nearest.is_finite() {
                                    let col = if nearest < 0.8 { C_WARN } else { C_AMBER };
                                    RichText::new(format!("{:.2} m", nearest))
                                        .font(FontId::monospace(22.0))
                                        .strong()
                                        .color(col)
                                } else {
                                    RichText::new("淨空")
                                        .font(FontId::monospace(22.0))
                                        .color(C_OK)
                                },
                            );
                            ui.label(
                                RichText::new(format!("{} 個方向有障礙", obst_cnt))
                                    .size(11.0)
                                    .color(C_MUTED),
                            );
                        });
                        if self.robot.avoiding {
                            ui.add_space(8.0);
                            ui.label(
                                RichText::new("⚠ 避障中")
                                    .font(FontId::proportional(16.0))
                                    .strong()
                                    .color(C_AMBER),
                            );
                        }
                    });
                });
                ui.add_space(8.0);
                } // if avoid_on

                egui::CollapsingHeader::new(
                    RichText::new("▶ CONTROL")
                        .font(FontId::monospace(13.0))
                        .color(C_TEXT),
                )
                .default_open(false)
                .show(ui, |ui| {
                    let mut changed = false;
                    changed |= ui.checkbox(&mut self.pose, "偵測人 (Pose)").changed();
                    changed |= ui.checkbox(&mut self.hands, "偵測手勢 (Hands)").changed();
                    changed |= ui.checkbox(&mut self.depth, "深度圖").changed();
                    changed |= ui
                        .checkbox(&mut self.dfilter, "深度濾鏡 (去噪/補洞)")
                        .changed();
                    changed |= ui.checkbox(&mut self.flip, "鏡像").changed();
                    changed |= ui
                        .checkbox(&mut self.measure, "📦 點選物體量測 (3D)")
                        .changed();
                    if changed {
                        self.send_cfg();
                    }
                    // 避障是前端模擬，不需通知後端
                    ui.checkbox(&mut self.avoid_on, "🛡 障礙物避障");
                    // 水平校正在後端執行（讓黃框/量測一起轉正）
                    if ui
                        .checkbox(&mut self.level_on, "⊿ IMU 自動水平")
                        .changed()
                    {
                        self.send_cfg();
                    }

                    // 畫面縮放 / 正方形畫布大小
                    ui.add_space(4.0);
                    label_mono(ui, "畫面顯示");
                    let mut disp_changed = false;
                    disp_changed |= ui
                        .add(
                            egui::Slider::new(&mut self.zoom, 0.5..=2.5)
                                .step_by(0.05)
                                .text("放大倍率"),
                        )
                        .changed();
                    disp_changed |= ui
                        .add(
                            egui::Slider::new(&mut self.disp, 480.0..=1100.0)
                                .step_by(20.0)
                                .text("畫布(px)"),
                        )
                        .changed();
                    if disp_changed {
                        self.send_cfg();
                    }

                    ui.add_space(4.0);
                    if ui.button("■ 擷取 3D 點雲").clicked() {
                        self.send_json(serde_json::json!({"scan": true}));
                    }
                });

                egui::CollapsingHeader::new(
                    RichText::new("▶ HELP")
                        .font(FontId::monospace(13.0))
                        .color(C_TEXT),
                )
                .default_open(false)
                .show(ui, |ui| {
                    ui.label(
                        RichText::new(
                            "手勢 1前進 4後退 5歸位 0停止\n\
                             2 左轉(維持前進/後退) 3 右轉(維持前進/後退)\n\
                             鍵盤 ↑前進 ↓後退 ←左轉 →右轉 空白停 R歸位",
                        )
                        .size(11.5)
                        .color(C_MUTED),
                    );
                });

                if !self.log.is_empty() {
                    ui.add_space(6.0);
                    ui.label(
                        RichText::new(&self.log)
                            .font(FontId::monospace(11.0))
                            .color(C_OK),
                    );
                }
                }); // ScrollArea
            });

        // ===== 中央：機器人競技場（障礙地圖，可收合）=====
        egui::CentralPanel::default()
            .frame(
                egui::Frame::none()
                    .fill(C_BG)
                    .inner_margin(Margin::same(12.0)),
            )
            .show(ctx, |ui| {
                // 收合切換列
                ui.horizontal(|ui| {
                    let arrow = if self.show_arena { "▼" } else { "▶" };
                    if ui
                        .add(
                            egui::Label::new(
                                RichText::new(format!("{} SIMULATION ARENA · 障礙地圖", arrow))
                                    .font(FontId::monospace(14.0))
                                    .strong()
                                    .color(C_ACCENT),
                            )
                            .sense(egui::Sense::click()),
                        )
                        .clicked()
                    {
                        self.show_arena = !self.show_arena;
                    }
                    if !self.show_arena {
                        ui.label(
                            RichText::new("(已收合，點此展開)")
                                .size(12.0)
                                .color(C_MUTED),
                        );
                    }
                });

                if self.show_arena {
                    ui.add_space(4.0);
                    let avail = ui.available_size();
                    let (resp, painter) = ui.allocate_painter(avail, egui::Sense::hover());
                    let rect = resp.rect;
                    self.robot.w = rect.width();
                    self.robot.h = rect.height();
                    if !self.sized {
                        self.robot.reset();
                        self.sized = true;
                    }
                    // 把後端的方向距離轉成競技場座標的障礙點
                    let obstacles =
                        obstacle_points(&self.status.obstacles, rect.width(), rect.height());
                    self.robot.step(&obstacles, self.avoid_on);
                    self.robot.draw(
                        &painter,
                        rect,
                        self.robot_tex.as_ref(),
                        t,
                        &obstacles,
                        self.avoid_on,
                    );
                } else {
                    // 收合時仍讓機器人持續移動(無障礙、無避障)
                    self.robot.step(&[], false);
                }
            });

        // ===== 跌倒警報 (彈出 + 確認按鈕) =====
        if self.fall_alarm {
            // 變暗背景
            let dim = ctx.layer_painter(egui::LayerId::new(
                egui::Order::Foreground,
                egui::Id::new("fall_dim"),
            ));
            dim.rect_filled(
                ctx.screen_rect(),
                0.0,
                Color32::from_rgba_unmultiplied(0, 0, 0, 170),
            );
            let flash = 0.5 + 0.5 * (t * 6.0).sin();
            egui::Window::new("fall_alarm")
                .title_bar(false)
                .resizable(false)
                .collapsible(false)
                .anchor(egui::Align2::CENTER_CENTER, [0.0, 0.0])
                .frame(
                    egui::Frame::none()
                        .fill(Color32::from_rgb(38, 12, 12))
                        .stroke(Stroke::new(3.0, C_WARN.gamma_multiply(0.5 + 0.5 * flash)))
                        .rounding(Rounding::same(14.0))
                        .inner_margin(Margin::same(30.0)),
                )
                .show(ctx, |ui| {
                    ui.set_width(360.0);
                    ui.vertical_centered(|ui| {
                        ui.label(RichText::new("⚠").size(60.0).color(C_WARN));
                        ui.label(
                            RichText::new("偵測到有人跌倒 / 躺下！")
                                .font(FontId::proportional(26.0))
                                .strong()
                                .color(Color32::WHITE),
                        );
                        ui.add_space(6.0);
                        ui.label(
                            RichText::new(format!(
                                "軀幹傾角 {:.0}°   ·   第 {} 次警報",
                                self.status.tilt, self.fall_count
                            ))
                            .size(14.0)
                            .color(Color32::from_rgb(210, 180, 180)),
                        );
                        ui.add_space(6.0);
                        ui.label(
                            RichText::new("📷 已自動截圖存證 → falls/ 資料夾")
                                .size(13.0)
                                .color(C_AMBER),
                        );
                        ui.add_space(18.0);
                        let btn = egui::Button::new(
                            RichText::new("✓  我知道了 (確認)")
                                .size(18.0)
                                .strong()
                                .color(C_BG),
                        )
                        .fill(C_AMBER)
                        .min_size(egui::vec2(240.0, 46.0));
                        if ui.add(btn).clicked() {
                            self.fall_alarm = false;
                        }
                    });
                });
        }

        ctx.request_repaint();
    }
}

fn rasterize_svg(bytes: &[u8], size: u32) -> Option<egui::ColorImage> {
    let opt = resvg::usvg::Options::default();
    let tree = resvg::usvg::Tree::from_data(bytes, &opt).ok()?;
    let mut pixmap = resvg::tiny_skia::Pixmap::new(size, size)?;
    let ts = resvg::tiny_skia::Transform::from_scale(
        size as f32 / tree.size().width(),
        size as f32 / tree.size().height(),
    );
    resvg::render(&tree, ts, &mut pixmap.as_mut());
    let mut img = egui::ColorImage::new([size as usize, size as usize], Color32::TRANSPARENT);
    for (i, px) in pixmap.pixels().iter().enumerate() {
        img.pixels[i] =
            Color32::from_rgba_premultiplied(px.red(), px.green(), px.blue(), px.alpha());
    }
    Some(img)
}

fn setup_fonts(ctx: &egui::Context) {
    let mut fonts = egui::FontDefinitions::default();
    // CJK：微軟正黑體
    if let Ok(bytes) = std::fs::read("C:/Windows/Fonts/msjh.ttc") {
        fonts
            .font_data
            .insert("cjk".to_owned(), egui::FontData::from_owned(bytes));
        fonts
            .families
            .get_mut(&egui::FontFamily::Proportional)
            .unwrap()
            .insert(0, "cjk".to_owned());
        // 等寬字也要能 fallback 顯示中文
        fonts
            .families
            .get_mut(&egui::FontFamily::Monospace)
            .unwrap()
            .push("cjk".to_owned());
    }
    // 技術等寬字：Consolas（數值/英文用）
    if let Ok(bytes) = std::fs::read("C:/Windows/Fonts/consola.ttf") {
        fonts
            .font_data
            .insert("mono".to_owned(), egui::FontData::from_owned(bytes));
        fonts
            .families
            .get_mut(&egui::FontFamily::Monospace)
            .unwrap()
            .insert(0, "mono".to_owned());
    }
    ctx.set_fonts(fonts);
}

fn setup_theme(ctx: &egui::Context) {
    let mut style = (*ctx.style()).clone();
    let v = &mut style.visuals;
    v.dark_mode = true;
    v.override_text_color = Some(C_TEXT);
    v.panel_fill = C_BG;
    v.window_fill = C_PANEL;
    v.extreme_bg_color = Color32::from_rgb(6, 9, 13);
    v.faint_bg_color = C_PANEL;
    v.hyperlink_color = C_ACCENT;
    v.selection.bg_fill = C_ACCENT.gamma_multiply(0.35);
    v.selection.stroke = Stroke::new(1.0, C_ACCENT);
    v.window_rounding = Rounding::same(10.0);
    v.window_stroke = Stroke::new(1.0, C_BORDER);

    let r = Rounding::same(6.0);
    v.widgets.noninteractive.bg_fill = C_PANEL;
    v.widgets.noninteractive.fg_stroke = Stroke::new(1.0, C_TEXT);
    v.widgets.noninteractive.bg_stroke = Stroke::new(1.0, C_BORDER);
    v.widgets.inactive.bg_fill = C_PANEL2;
    v.widgets.inactive.weak_bg_fill = C_PANEL2;
    v.widgets.inactive.fg_stroke = Stroke::new(1.0, C_TEXT);
    v.widgets.inactive.rounding = r;
    v.widgets.hovered.bg_fill = Color32::from_rgb(32, 44, 60);
    v.widgets.hovered.weak_bg_fill = Color32::from_rgb(32, 44, 60);
    v.widgets.hovered.fg_stroke = Stroke::new(1.0, C_ACCENT);
    v.widgets.hovered.bg_stroke = Stroke::new(1.0, C_ACCENT);
    v.widgets.hovered.rounding = r;
    v.widgets.active.bg_fill = C_ACCENT;
    v.widgets.active.weak_bg_fill = C_ACCENT;
    v.widgets.active.fg_stroke = Stroke::new(1.0, C_BG);
    v.widgets.active.rounding = r;

    style.spacing.item_spacing = egui::vec2(8.0, 7.0);
    style.spacing.button_padding = egui::vec2(10.0, 6.0);
    ctx.set_style(style);
}

fn main() -> eframe::Result<()> {
    if std::env::args().any(|a| a == "--svgtest") {
        let opt = resvg::usvg::Options::default();
        let tree = resvg::usvg::Tree::from_data(ROBOT_SVG, &opt).expect("svg parse");
        let mut pm = resvg::tiny_skia::Pixmap::new(256, 256).unwrap();
        let ts = resvg::tiny_skia::Transform::from_scale(
            256.0 / tree.size().width(),
            256.0 / tree.size().height(),
        );
        resvg::render(&tree, ts, &mut pm.as_mut());
        pm.save_png("robot_preview.png").expect("save png");
        println!("wrote robot_preview.png");
        return Ok(());
    }

    // 偵錯：headless 模擬避障 —— 開往一道有缺口的牆，看是否繞過
    if std::env::args().any(|a| a == "--avoidtest") {
        let mut r = RobotSim::new(600.0, 400.0);
        r.action = "FORWARD".into(); // 朝上(y 減小)
        let mut obs = Vec::new();
        let mut x = 60.0;
        while x <= 330.0 {
            obs.push((x, 110.0)); // 上方一道牆，右側 x>330 留缺口
            x += 16.0;
        }
        let start = (r.x, r.y);
        let mut avoided = false;
        let mut min_d = f32::MAX;
        let mut passed = false;
        for _ in 0..500 {
            r.step(&obs, true);
            if r.avoiding {
                avoided = true;
            }
            for &(ox, oy) in &obs {
                let d = ((ox - r.x).powi(2) + (oy - r.y).powi(2)).sqrt();
                if d < min_d {
                    min_d = d;
                }
            }
            if r.y < 95.0 {
                passed = true; // 越過牆面
            }
        }
        println!(
            "avoidtest: start=({:.0},{:.0}) end=({:.0},{:.0}) avoided={} passed_wall={} min_obstacle_dist={:.1}",
            start.0, start.1, r.x, r.y, avoided, passed, min_d
        );
        let ok = avoided && min_d > 18.0;
        println!("result: {}", if ok { "PASS (避障觸發、未撞穿)" } else { "CHECK" });
        return Ok(());
    }

    let opts = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default().with_inner_size([1320.0, 880.0]),
        ..Default::default()
    };
    eframe::run_native(
        "D435i 機器人視覺 (Rust)",
        opts,
        Box::new(|cc| Ok(Box::new(App::new(cc)))),
    )
}
