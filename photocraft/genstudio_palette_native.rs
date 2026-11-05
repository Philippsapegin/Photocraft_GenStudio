//! Native ownership and physical placement of Studio. No new unsafe code.
#[derive(Default)]
pub struct Hooks {
    #[cfg(target_os = "windows")]
    root: std::sync::atomic::AtomicIsize,
    #[cfg(target_os = "windows")]
    palette: std::sync::atomic::AtomicIsize,
}
#[cfg(not(target_os = "windows"))]
impl eframe::ViewportHooks for Hooks {}

#[cfg(target_os = "windows")]
mod platform {
    use super::Hooks;
    use eframe::wgpu::rwh::{HasWindowHandle, RawWindowHandle};
    use std::sync::atomic::Ordering;
    use winit::{
        dpi::PhysicalPosition,
        platform::windows::WindowAttributesExtWindows,
        window::{Window, WindowAttributes},
    };
    fn raw(window: &Window) -> Option<isize> {
        match window.window_handle().ok()?.as_raw() {
            RawWindowHandle::Win32(handle) => Some(handle.hwnd.get()),
            _ => None,
        }
    }
    fn owner_rect(target: isize) -> Option<winsafe::RECT> {
        let mut rect = None;
        let _ = winsafe::EnumWindows(|window| {
            if window.ptr() as isize == target {
                rect = window.GetWindowRect().ok();
                false
            } else {
                true
            }
        });
        rect
    }
    impl eframe::ViewportHooks for Hooks {
        fn window_attributes(
            &self,
            ctx: &egui::Context,
            viewport: egui::ViewportId,
            attributes: &mut WindowAttributes,
        ) {
            if viewport != photocraft_ui_egui::genstudio_palette::id() {
                return;
            }
            let root = self.root.load(Ordering::Relaxed);
            if root != 0 {
                *attributes = attributes.clone().with_owner_window(root as _);
            }
            if let Some(p) = photocraft_ui_egui::genstudio_palette::requested_position(ctx) {
                *attributes = attributes
                    .clone()
                    .with_position(PhysicalPosition::new(p.x as i32, p.y as i32));
            } else if let Some(rect) = owner_rect(root) {
                let width = (940.0 * ctx.pixels_per_point()).round() as i32;
                let height = (650.0 * ctx.pixels_per_point()).round() as i32;
                *attributes = attributes.clone().with_position(PhysicalPosition::new(
                    rect.left + (rect.right - rect.left - width) / 2,
                    rect.top + (rect.bottom - rect.top - height) / 2,
                ));
            }
        }
        fn window_created(&self, viewport: egui::ViewportId, window: &Window) {
            let Some(handle) = raw(window) else { return };
            if viewport == egui::ViewportId::ROOT {
                self.root.store(handle, Ordering::Relaxed);
            }
            if viewport != photocraft_ui_egui::genstudio_palette::id() {
                return;
            }
            self.palette.store(handle, Ordering::Relaxed);
            crate::window_frame::palette_frame(handle);
            // Keep the title reachable when a saved monitor has been disconnected.
            if let Ok(at) = window.outer_position() {
                let monitors: Vec<_> = window.available_monitors().collect();
                let visible = monitors.iter().any(|m| {
                    let p = m.position();
                    let s = m.size();
                    at.x >= p.x
                        && at.y >= p.y
                        && i64::from(at.x) + 64 <= i64::from(p.x) + i64::from(s.width)
                        && i64::from(at.y) + 24 <= i64::from(p.y) + i64::from(s.height)
                });
                if !visible
                    && let Some(monitor) = window
                        .current_monitor()
                        .or_else(|| monitors.first().cloned())
                {
                    let p = monitor.position();
                    let s = monitor.size();
                    let size = window.outer_size();
                    window.set_outer_position(PhysicalPosition::new(
                        p.x + (s.width.saturating_sub(size.width) / 2) as i32,
                        p.y + (s.height.saturating_sub(size.height) / 2) as i32,
                    ));
                }
            }
        }
        fn window_destroyed(&self, viewport: egui::ViewportId) {
            if viewport == photocraft_ui_egui::genstudio_palette::id() {
                self.palette.store(0, Ordering::Relaxed);
            }
        }
        fn raw_input(&self, ctx: &egui::Context, input: &mut egui::RawInput) {
            if input.viewport_id != photocraft_ui_egui::genstudio_palette::id() {
                return;
            }
            let handle = self.palette.load(Ordering::Relaxed);
            if handle != 0 {
                crate::window_frame::palette_frame(handle);
            }
            photocraft_ui_egui::genstudio_palette::raw_input(ctx, input);
        }
    }
}
