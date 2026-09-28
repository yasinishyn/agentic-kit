// Kanban desktop shell (PRD-07). Prevents an extra console window on Windows in release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    kanban_app_lib::run();
}
