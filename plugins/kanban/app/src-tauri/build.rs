// The app manifest makes every app command need an explicit permission (allow-term-open, …). They are granted only
// by the runtime capability built in lib.rs for the main window on the exact daemon origin (architecture §8).
fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(
            tauri_build::AppManifest::new().commands(&["term_open", "term_write", "term_resize", "term_close"]),
        ),
    )
    .expect("failed to run tauri-build");
}
