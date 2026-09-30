// The app manifest makes every app command need an explicit permission (allow-term-open, …, allow-pick-folder). They
// are granted only by the runtime capability built in scope.rs for the main window on the exact daemon origin
// (architecture §8). The dialog plugin's own `dialog:*` permissions are never granted.
fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(tauri_build::AppManifest::new().commands(&[
            "term_open",
            "term_write",
            "term_resize",
            "term_close",
            "pick_folder",
        ])),
    )
    .expect("failed to run tauri-build");
}
