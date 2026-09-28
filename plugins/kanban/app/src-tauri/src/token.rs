//! UI token hand-over (ADR-007): Rust reads ui.token and injects it with an initialization script that only acts on
//! the daemon origin. The window URL never carries the token; nothing here logs it.
use url::Url;

/// "http://127.0.0.1:<port>" — the only origin the daemon serves the board on.
pub fn daemon_origin(port: u16) -> String {
    format!("http://127.0.0.1:{port}")
}

/// The URL the main window loads (no token, no fragment).
pub fn window_url(origin: &str) -> Url {
    Url::parse(&format!("{origin}/")).expect("daemon origin is a valid URL")
}

/// The initialization script defining window.__KANBAN_TOKEN__, or None when `origin` is not a loopback daemon origin.
/// The script re-checks location.origin at run time, so a page from any other origin never sees the token.
pub fn init_script(origin: &str, token: &str) -> Option<String> {
    let parsed = Url::parse(origin).ok()?;
    let exact = parsed.scheme() == "http"
        && parsed.host_str() == Some("127.0.0.1")
        && parsed.port().is_some()
        && parsed.origin().ascii_serialization() == origin;
    if !exact {
        return None;
    }
    let origin_js = serde_json::to_string(origin).ok()?;
    let token_js = serde_json::to_string(token).ok()?;
    Some(format!(
        "(function(){{if(window.location.origin!=={origin_js})return;\
         Object.defineProperty(window,\"__KANBAN_TOKEN__\",\
         {{value:{token_js},writable:false,enumerable:false,configurable:false}});}})();"
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    const TOKEN: &str = "tok-SECRET_abc123";

    #[test]
    fn token_injection_origin_only() {
        let origin = daemon_origin(47821);
        assert_eq!(origin, "http://127.0.0.1:47821");
        let url = window_url(&origin);
        assert_eq!(url.as_str(), "http://127.0.0.1:47821/");
        assert!(!url.as_str().contains(TOKEN));
        assert!(url.fragment().is_none() && url.query().is_none());

        let script = init_script(&origin, TOKEN).expect("script for the daemon origin");
        let guard = script.find(r#"window.location.origin!=="http://127.0.0.1:47821""#).expect("origin guard");
        let tok = script.find(r#""tok-SECRET_abc123""#).expect("token literal");
        assert!(guard < tok, "the origin check runs before the token is defined");
        assert!(script.contains("__KANBAN_TOKEN__"));
        assert!(script.contains("writable:false") && script.contains("configurable:false"));

        // only for a loopback http origin with a port, never for anything else
        assert_eq!(init_script("https://example.com", TOKEN), None);
        assert_eq!(init_script("http://localhost:47821", TOKEN), None);
        assert_eq!(init_script("http://127.0.0.1:47821/path", TOKEN), None);
        assert_eq!(init_script("tauri://localhost", TOKEN), None);
        // a token can never break out of its string literal
        let s = init_script(&origin, "a\"</script>\\").unwrap();
        assert!(s.contains(r#""a\"</script>\\""#));
    }
}
