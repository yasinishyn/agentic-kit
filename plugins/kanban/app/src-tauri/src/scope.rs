//! IPC scope (architecture §8): the app commands (terminal + folder picker) only for the main window showing the exact
//! daemon origin. The runtime capability grants them there; every command re-checks with `terminal_allowed` /
//! `pick_folder_check` (defence in depth). No `dialog:*` permission is ever granted: the page cannot reach the dialog
//! plugin's own commands, only `pick_folder`.
use serde_json::{json, Value};
use url::Url;

pub const MAIN: &str = "main";
pub const TERM_COMMANDS: [&str; 4] = ["term_open", "term_write", "term_resize", "term_close"];
pub const PICK_FOLDER: &str = "pick_folder";

/// The runtime capability (JSON accepted by `Manager::add_capability`): main window, remote url = exact origin.
pub fn capability(origin: &str) -> Value {
    let mut permissions = vec![Value::from("core:event:default")];
    let allow = |c: &str| Value::from(format!("allow-{}", c.replace('_', "-")));
    permissions.extend(TERM_COMMANDS.iter().map(|c| allow(c)));
    permissions.push(allow(PICK_FOLDER));
    json!({
        "identifier": "kanban-board",
        "description": "The board page on the daemon origin: terminal and folder-picker commands and events, main window only",
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

/// `pick_folder` re-check: the same rule as the terminal commands; `origin` is None while no board is running.
pub fn pick_folder_check(window_label: &str, webview_url: Option<&Url>, origin: Option<&str>) -> Result<(), &'static str> {
    match origin {
        Some(o) if terminal_allowed(window_label, webview_url, o) => Ok(()),
        _ => Err("not allowed"),
    }
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
                               "allow-term-close", "allow-pick-folder"]);
        assert!(!perms.iter().any(|p| p.starts_with("dialog:")), "{perms:?}");
        assert!(cap.get("webviews").is_none());
    }

    #[test]
    fn pick_folder_scope() {
        let origin = "http://127.0.0.1:47821";
        let board = Url::parse("http://127.0.0.1:47821/?project=p1").unwrap();
        assert_eq!(pick_folder_check("main", Some(&board), Some(origin)), Ok(()));
        for (label, url) in [
            ("settings", Some("http://127.0.0.1:47821/")),
            ("", Some("http://127.0.0.1:47821/")),
            ("main", Some("https://example.com/")),
            ("main", Some("http://127.0.0.1:1/")),
            ("main", Some("http://localhost:47821/")),
            ("main", Some("tauri://localhost/index.html")),
            ("main", None),
        ] {
            let url = url.map(|u| Url::parse(u).unwrap());
            assert_eq!(pick_folder_check(label, url.as_ref(), Some(origin)), Err("not allowed"), "{label} {url:?}");
        }
        // no board running (status/error page): nothing is allowed
        assert_eq!(pick_folder_check("main", Some(&board), None), Err("not allowed"));
    }
}
