//! Navigation lock (PRD-07, architecture §8): the window shows only the daemon origin and the app's own status page;
//! web links open in the default browser; every other scheme is dropped.
use url::Url;

#[derive(Debug, PartialEq, Eq)]
pub enum Nav {
    Allow,
    External,
    Block,
}

/// Schemes handed to the default browser / OS (vscode: the board's "open in editor" links).
pub const EXTERNAL_SCHEMES: [&str; 4] = ["http", "https", "mailto", "vscode"];

pub fn decide(url: &Url, daemon_origin: Option<&str>) -> Nav {
    let scheme = url.scheme();
    if url.as_str() == "about:blank" || (scheme == "tauri" && url.host_str() == Some("localhost")) {
        return Nav::Allow;
    }
    if scheme == "http" || scheme == "https" {
        if daemon_origin.is_some_and(|o| url.origin().ascii_serialization() == o) {
            return Nav::Allow;
        }
        return Nav::External;
    }
    if EXTERNAL_SCHEMES.contains(&scheme) {
        Nav::External
    } else {
        Nav::Block
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn d(u: &str, origin: Option<&str>) -> Nav {
        decide(&Url::parse(u).unwrap(), origin)
    }

    #[test]
    fn nav_guard_blocks_foreign_origin() {
        let o = Some("http://127.0.0.1:47821");
        assert_eq!(d("https://example.com/", o), Nav::External);
        assert_eq!(d("https://example.com/", None), Nav::External);
        assert_eq!(d("http://127.0.0.1:47821/", o), Nav::Allow);
        assert_eq!(d("http://127.0.0.1:47821/ui/index.html?x=1#y", o), Nav::Allow);
        // same host, other port or other host name: not the daemon origin
        assert_eq!(d("http://127.0.0.1:9999/", o), Nav::External);
        assert_eq!(d("http://localhost:47821/", o), Nav::External);
        assert_eq!(d("https://127.0.0.1:47821/", o), Nav::External);
        // before the daemon is known only the app page is shown
        assert_eq!(d("http://127.0.0.1:47821/", None), Nav::External);
        assert_eq!(d("tauri://localhost/index.html", o), Nav::Allow);
        assert_eq!(d("tauri://evil/index.html", o), Nav::Block);
        assert_eq!(d("about:blank", o), Nav::Allow);
        assert_eq!(d("vscode://file/tmp/x.md", o), Nav::External);
        assert_eq!(d("mailto:a@example.com", o), Nav::External);
        assert_eq!(d("file:///etc/passwd", o), Nav::Block);
        assert_eq!(d("javascript:alert(1)", o), Nav::Block);
        assert_eq!(d("data:text/html,hi", o), Nav::Block);
    }
}
