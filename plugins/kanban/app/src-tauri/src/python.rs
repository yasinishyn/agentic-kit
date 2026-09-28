//! python3 resolution (PRD-07): the login shell's `command -v python3` first, then fixed fallbacks; 3.9 or newer.
use std::path::{Path, PathBuf};

pub const FALLBACKS: [&str; 3] = ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"];
pub const MIN_VERSION: (u32, u32) = (3, 9);

#[derive(Debug, PartialEq, Eq)]
pub enum PythonError {
    Missing,
    TooOld { path: PathBuf, version: (u32, u32) },
}

/// Candidates in order: the login-shell answer (only if it is an absolute path), then FALLBACKS, without duplicates.
pub fn candidates(login_shell_output: Option<&str>, fallbacks: &[&str]) -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = Vec::new();
    let login = login_shell_output.map(str::trim).filter(|s| s.starts_with('/') && !s.contains('\n'));
    for c in login.into_iter().chain(fallbacks.iter().copied()) {
        let p = PathBuf::from(c);
        if !out.contains(&p) {
            out.push(p);
        }
    }
    out
}

/// "Python 3.11.4" → (3, 11)
pub fn parse_version(output: &str) -> Option<(u32, u32)> {
    let rest = output.trim().strip_prefix("Python ")?;
    let mut parts = rest.split(|c: char| c == '.' || c.is_whitespace());
    let major = parts.next()?.parse().ok()?;
    let minor = parts.next()?.parse().ok()?;
    Some((major, minor))
}

/// The first candidate whose version (None: missing / not runnable) is at least MIN_VERSION.
pub fn resolve(
    candidates: &[PathBuf],
    version_of: impl Fn(&Path) -> Option<(u32, u32)>,
) -> Result<PathBuf, PythonError> {
    let mut too_old = None;
    for c in candidates {
        match version_of(c) {
            Some(v) if v >= MIN_VERSION => return Ok(c.clone()),
            Some(v) if too_old.is_none() => too_old = Some(PythonError::TooOld { path: c.clone(), version: v }),
            _ => {}
        }
    }
    Err(too_old.unwrap_or(PythonError::Missing))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn versions(pairs: &[(&str, (u32, u32))]) -> impl Fn(&Path) -> Option<(u32, u32)> {
        let map: HashMap<PathBuf, (u32, u32)> = pairs.iter().map(|(p, v)| (PathBuf::from(p), *v)).collect();
        move |p: &Path| map.get(p).copied()
    }

    #[test]
    fn python_resolution_order() {
        let fb = ["/fake/homebrew/python3", "/fake/local/python3", "/fake/usr/python3"];
        // login shell first
        let c = candidates(Some("/Users/u/.pyenv/shims/python3\n"), &fb);
        assert_eq!(c[0], PathBuf::from("/Users/u/.pyenv/shims/python3"));
        assert_eq!(c.len(), 4);
        let v = versions(&[("/Users/u/.pyenv/shims/python3", (3, 12)), ("/fake/homebrew/python3", (3, 13))]);
        assert_eq!(resolve(&c, v), Ok(PathBuf::from("/Users/u/.pyenv/shims/python3")));
        // no login answer: fallbacks in order, skipping missing ones
        let c = candidates(None, &fb);
        let v = versions(&[("/fake/local/python3", (3, 11)), ("/fake/usr/python3", (3, 9))]);
        assert_eq!(resolve(&c, v), Ok(PathBuf::from("/fake/local/python3")));
        // a non-path login answer is ignored; a duplicate is not tried twice
        let c = candidates(Some("python3 not found"), &fb);
        assert_eq!(c, fb.iter().map(PathBuf::from).collect::<Vec<_>>());
        let c = candidates(Some("/fake/local/python3"), &fb);
        assert_eq!(c, vec![PathBuf::from("/fake/local/python3"), PathBuf::from("/fake/homebrew/python3"),
                           PathBuf::from("/fake/usr/python3")]);
        // too old login python → next one that is new enough
        let c = candidates(Some("/old/python3"), &fb);
        let v = versions(&[("/old/python3", (3, 8)), ("/fake/homebrew/python3", (3, 10))]);
        assert_eq!(resolve(&c, v), Ok(PathBuf::from("/fake/homebrew/python3")));
        // errors
        assert_eq!(resolve(&candidates(None, &fb), versions(&[])), Err(PythonError::Missing));
        assert_eq!(resolve(&candidates(None, &fb), versions(&[("/fake/usr/python3", (3, 8))])),
                   Err(PythonError::TooOld { path: PathBuf::from("/fake/usr/python3"), version: (3, 8) }));
        assert_eq!(FALLBACKS, ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]);
    }

    #[test]
    fn python_version_parsing() {
        assert_eq!(parse_version("Python 3.11.4\n"), Some((3, 11)));
        assert_eq!(parse_version("Python 3.9.6"), Some((3, 9)));
        assert_eq!(parse_version("Python 2.7"), Some((2, 7)));
        assert_eq!(parse_version("xcode-select: note: no developer tools"), None);
    }
}
