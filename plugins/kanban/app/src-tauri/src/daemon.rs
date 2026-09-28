//! Talking to the board daemon: KANBAN_HOME, daemon.json, ui.token (never logged), a tiny loopback HTTP GET, the
//! project registry lookup behind term_open and the "Stop board service" enablement rule.
use serde::Deserialize;
use std::io::{Read, Write};
use std::net::{Ipv4Addr, SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::time::Duration;

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct DaemonInfo {
    pub port: u16,
    pub pid: i64,
    pub api: i64,
    pub version: String,
}

/// KANBAN_HOME if set, else ~/Library/Application Support/Kanban (macOS) or $XDG_DATA_HOME/kanban.
pub fn kanban_home(env_home: Option<String>, user_home: Option<String>, xdg_data: Option<String>) -> PathBuf {
    if let Some(h) = env_home.filter(|h| !h.is_empty()) {
        return PathBuf::from(h);
    }
    let home = PathBuf::from(user_home.unwrap_or_else(|| "/".into()));
    if cfg!(target_os = "macos") {
        return home.join("Library/Application Support/Kanban");
    }
    match xdg_data.filter(|x| !x.is_empty()) {
        Some(x) => PathBuf::from(x).join("kanban"),
        None => home.join(".local/share/kanban"),
    }
}

pub fn parse_info(text: &str) -> Result<DaemonInfo, String> {
    serde_json::from_str(text).map_err(|e| format!("daemon.json is not readable: {e}"))
}

/// The token file's content, trimmed. Errors never include the value.
pub fn read_token(path: &Path) -> Result<String, String> {
    let text = std::fs::read_to_string(path).map_err(|e| format!("cannot read {}: {e}", path.display()))?;
    let value = text.trim();
    if value.is_empty() {
        return Err(format!("{} is empty", path.display()));
    }
    Ok(value.to_string())
}

/// GET http://127.0.0.1:<port><path> with the bearer token → (status, body).
pub fn http_get(port: u16, path: &str, token: &str) -> Result<(u16, String), String> {
    let addr = SocketAddr::from((Ipv4Addr::LOCALHOST, port));
    let mut stream = TcpStream::connect_timeout(&addr, Duration::from_secs(3)).map_err(|e| e.to_string())?;
    stream.set_read_timeout(Some(Duration::from_secs(10))).map_err(|e| e.to_string())?;
    let request = format!(
        "GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer {token}\r\n\
         Accept: application/json\r\nConnection: close\r\n\r\n"
    );
    stream.write_all(request.as_bytes()).map_err(|e| e.to_string())?;
    let mut raw = Vec::new();
    stream.read_to_end(&mut raw).map_err(|e| e.to_string())?;
    let text = String::from_utf8_lossy(&raw);
    let (head, body) = text.split_once("\r\n\r\n").ok_or("malformed HTTP response")?;
    let status = head
        .lines()
        .next()
        .and_then(|l| l.split_whitespace().nth(1))
        .and_then(|c| c.parse().ok())
        .ok_or("malformed HTTP status line")?;
    Ok((status, body.to_string()))
}

/// The registry root of `project_id` from a `GET /api/projects` body (never a path supplied by the page).
pub fn project_root(projects_body: &str, project_id: &str) -> Option<PathBuf> {
    let data: serde_json::Value = serde_json::from_str(projects_body).ok()?;
    let root = data["projects"]
        .as_array()?
        .iter()
        .find(|p| p["id"].as_str() == Some(project_id))?["root"]
        .as_str()?;
    let path = PathBuf::from(root);
    path.is_absolute().then_some(path)
}

/// "Stop board service" is disabled while `GET /api/runs?live=1` reports runs; enabled when the endpoint is absent.
pub fn stop_enabled(response: &Result<(u16, String), String>) -> bool {
    match response {
        Ok((200, body)) => serde_json::from_str::<serde_json::Value>(body)
            .ok()
            .and_then(|v| v["runs"].as_array().map(|runs| runs.is_empty()))
            .unwrap_or(true),
        _ => true,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;

    #[test]
    fn home_resolution() {
        assert_eq!(kanban_home(Some("/tmp/kh".into()), Some("/Users/u".into()), None), PathBuf::from("/tmp/kh"));
        assert_eq!(kanban_home(Some("".into()), Some("/Users/u".into()), None),
                   kanban_home(None, Some("/Users/u".into()), None));
        if cfg!(target_os = "macos") {
            assert_eq!(kanban_home(None, Some("/Users/u".into()), None),
                       PathBuf::from("/Users/u/Library/Application Support/Kanban"));
        } else {
            assert_eq!(kanban_home(None, Some("/home/u".into()), Some("/x".into())), PathBuf::from("/x/kanban"));
            assert_eq!(kanban_home(None, Some("/home/u".into()), None), PathBuf::from("/home/u/.local/share/kanban"));
        }
    }

    #[test]
    fn daemon_json() {
        let info = parse_info(r#"{"port": 47821, "pid": 12, "api": 1, "version": "0.3.0"}"#).unwrap();
        assert_eq!(info, DaemonInfo { port: 47821, pid: 12, api: 1, version: "0.3.0".into() });
        assert!(parse_info("{}").is_err());
    }

    #[test]
    fn term_open_resolves_project_through_registry() {
        let body = r#"{"projects": [{"id": "p1", "name": "a", "root": "/work/a"},
                                    {"id": "p2", "name": "b", "root": "/work/b"}]}"#;
        assert_eq!(project_root(body, "p2"), Some(PathBuf::from("/work/b")));
        assert_eq!(project_root(body, "/work/a"), None);
        assert_eq!(project_root(body, "p3"), None);
        assert_eq!(project_root(r#"{"projects": [{"id": "p", "root": "relative"}]}"#, "p"), None);
        assert_eq!(project_root("not json", "p1"), None);
    }

    #[test]
    fn stop_menu_rule() {
        assert!(!stop_enabled(&Ok((200, r#"{"runs": [{"id": "r1"}]}"#.into()))));
        assert!(stop_enabled(&Ok((200, r#"{"runs": []}"#.into()))));
        assert!(stop_enabled(&Ok((404, r#"{"error": "not found"}"#.into()))));
        assert!(stop_enabled(&Err("connection refused".into())));
    }

    #[test]
    fn http_get_sends_bearer_and_loopback_host() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = std::thread::spawn(move || {
            let (mut s, _) = listener.accept().unwrap();
            let mut buf = [0u8; 4096];
            let n = s.read(&mut buf).unwrap();
            let req = String::from_utf8_lossy(&buf[..n]).to_string();
            let body = r#"{"ok": true}"#;
            write!(s, "HTTP/1.0 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
                   body.len(), body).unwrap();
            req
        });
        let (status, body) = http_get(port, "/api/runs?live=1", "t0k").unwrap();
        let req = server.join().unwrap();
        assert_eq!(status, 200);
        assert_eq!(body, r#"{"ok": true}"#);
        assert!(req.starts_with("GET /api/runs?live=1 HTTP/1.1\r\n"), "{req}");
        assert!(req.contains(&format!("\r\nHost: 127.0.0.1:{port}\r\n")));
        assert!(req.contains("\r\nAuthorization: Bearer t0k\r\n"));
    }

    #[test]
    fn token_file_is_trimmed() {
        let dir = std::env::temp_dir().join(format!("kanban-app-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join("ui.token");
        std::fs::write(&p, "abc\n").unwrap();
        assert_eq!(read_token(&p).unwrap(), "abc");
        std::fs::write(&p, "\n").unwrap();
        assert!(read_token(&p).is_err());
        assert!(read_token(&dir.join("missing")).is_err());
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
