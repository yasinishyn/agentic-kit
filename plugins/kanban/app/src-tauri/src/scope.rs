//! IPC scope (architecture §8): terminal commands only for the main window showing the exact daemon origin. The
//! runtime capability grants them there; every command re-checks with `terminal_allowed` (defence in depth).
use serde_json::{json, Value};
use url::Url;

pub const MAIN: &str = "main";
pub const TERM_COMMANDS: [&str; 4] = ["term_open", "term_write", "term_resize", "term_close"];

/// The runtime capability (JSON accepted by `Manager::add_capability`): main window, remote url = exact origin.
pub fn capability(origin: &str) -> Value {
    let mut permissions = vec![Value::from("core:event:default")];
    permissions.extend(TERM_COMMANDS.iter().map(|c| Value::from(format!("allow-{}", c.replace('_', "-")))));
    json!({
        "identifier": "kanban-board",
        "description": "The board page on the daemon origin: terminal commands and events, main window only",
        "local": false,
        "windows": [MAIN],
        "remote": {"urls": [origin]},
        "permissions": permissions,
    })
}

pub fn terminal_allowed(window_label: &str, webview_url: Option<&Url>, daemon_origin: &str) -> bool {
    window_label == MAIN
        && webview_url.is_some_and(|u| {
            (u.scheme() == "http" || u.scheme() == "https") && u.origin().ascii_serialization() == daemon_origin
        })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn capability_scope() {
        let origin = "http://127.0.0.1:47821";
        let board = Url::parse("http://127.0.0.1:47821/").unwrap();
        assert!(terminal_allowed("main", Some(&board), origin));
        assert!(!terminal_allowed("settings", Some(&board), origin));
        assert!(!terminal_allowed("", Some(&board), origin));
        assert!(!terminal_allowed("main", Some(&Url::parse("https://example.com/").unwrap()), origin));
        assert!(!terminal_allowed("main", Some(&Url::parse("http://127.0.0.1:1/").unwrap()), origin));
        assert!(!terminal_allowed("main", Some(&Url::parse("tauri://localhost/index.html").unwrap()), origin));
        assert!(!terminal_allowed("main", None, origin));

        let cap = capability(origin);
        assert_eq!(cap["windows"], json!(["main"]));
        assert_eq!(cap["remote"]["urls"], json!(["http://127.0.0.1:47821"]));
        assert_eq!(cap["local"], json!(false));
        let perms: Vec<&str> = cap["permissions"].as_array().unwrap().iter().map(|p| p.as_str().unwrap()).collect();
        assert_eq!(perms, vec!["core:event:default", "allow-term-open", "allow-term-write", "allow-term-resize",
                               "allow-term-close"]);
        assert!(cap.get("webviews").is_none());
    }
}
