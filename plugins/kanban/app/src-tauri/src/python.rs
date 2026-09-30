//! python3 resolution (PRD-07; v0.3.1 PRD-01): the bundled runtime first, then the login shell's `command -v python3`,
//! then fixed fallbacks; 3.9 or newer.
use std::path::{Path, PathBuf};

pub const FALLBACKS: [&str; 3] = ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"];
pub const MIN_VERSION: (u32, u32) = (3, 9);

#[derive(Debug, PartialEq, Eq)]
pub enum PythonError {
    Missing,
    TooOld { path: PathBuf, version: (u32, u32) },
}

/// The bundled runtime's folder for a Rust/`uname -m` arch (v0.3.1 PRD-01, ADR-001): `aarch64`/`arm64` → `arm64`.
pub fn arch_dir(arch: &str) -> Option<&'static str> {
    match arch {
        "aarch64" | "arm64" => Some("arm64"),
        "x86_64" => Some("x86_64"),
        _ => None,
    }
}

/// `<resource_dir>/python/<arm64|x86_64>/bin/python3` (the contract shared with the plugin's `kpython`), present or not.
pub fn bundled_path(resource_dir: &Path, arch: &str) -> Option<PathBuf> {
    Some(resource_dir.join("python").join(arch_dir(arch)?).join("bin").join("python3"))
}

/// The bundled interpreter when the release build shipped it (the file exists), else `None`.
pub fn bundled(resource_dir: &Path, arch: &str) -> Option<PathBuf> {
    bundled_path(resource_dir, arch).filter(|p| p.is_file())
}

/// Candidates in order: the bundled interpreter (if any), the login-shell answer (only if it is an absolute path),
/// then FALLBACKS, without duplicates.
pub fn candidates(bundled: Option<&Path>, login_shell_output: Option<&str>, fallbacks: &[&str]) -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = bundled.map(Path::to_path_buf).into_iter().collect();
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
        let c = candidates(None, Some("/Users/u/.pyenv/shims/python3\n"), &fb);
        assert_eq!(c[0], PathBuf::from("/Users/u/.pyenv/shims/python3"));
        assert_eq!(c.len(), 4);
        let v = versions(&[("/Users/u/.pyenv/shims/python3", (3, 12)), ("/fake/homebrew/python3", (3, 13))]);
        assert_eq!(resolve(&c, v), Ok(PathBuf::from("/Users/u/.pyenv/shims/python3")));
        // no login answer: fallbacks in order, skipping missing ones
        let c = candidates(None, None, &fb);
        let v = versions(&[("/fake/local/python3", (3, 11)), ("/fake/usr/python3", (3, 9))]);
        assert_eq!(resolve(&c, v), Ok(PathBuf::from("/fake/local/python3")));
        // a non-path login answer is ignored; a duplicate is not tried twice
        let c = candidates(None, Some("python3 not found"), &fb);
        assert_eq!(c, fb.iter().map(PathBuf::from).collect::<Vec<_>>());
        let c = candidates(None, Some("/fake/local/python3"), &fb);
        assert_eq!(c, vec![PathBuf::from("/fake/local/python3"), PathBuf::from("/fake/homebrew/python3"),
                           PathBuf::from("/fake/usr/python3")]);
        // too old login python → next one that is new enough
        let c = candidates(None, Some("/old/python3"), &fb);
        let v = versions(&[("/old/python3", (3, 8)), ("/fake/homebrew/python3", (3, 10))]);
        assert_eq!(resolve(&c, v), Ok(PathBuf::from("/fake/homebrew/python3")));
        // errors
        assert_eq!(resolve(&candidates(None, None, &fb), versions(&[])), Err(PythonError::Missing));
        assert_eq!(resolve(&candidates(None, None, &fb), versions(&[("/fake/usr/python3", (3, 8))])),
                   Err(PythonError::TooOld { path: PathBuf::from("/fake/usr/python3"), version: (3, 8) }));
        assert_eq!(FALLBACKS, ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]);
    }

    #[test]
    fn python_resolution_prefers_bundled() {
        let fb = ["/fake/homebrew/python3", "/fake/local/python3", "/fake/usr/python3"];
        let res = std::env::temp_dir().join(format!("kanban-bundled-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&res);
        // the path contract shared with kpython: <resources>/python/<arm64|x86_64>/bin/python3, aarch64 → arm64
        assert_eq!(bundled_path(Path::new("/R"), "aarch64"), Some(PathBuf::from("/R/python/arm64/bin/python3")));
        assert_eq!(bundled_path(Path::new("/R"), "x86_64"), Some(PathBuf::from("/R/python/x86_64/bin/python3")));
        assert_eq!(bundled_path(Path::new("/R"), "riscv64"), None);
        // absent → None and the v0.3 order unchanged
        for arch in ["aarch64", "x86_64"] {
            assert_eq!(bundled(&res, arch), None);
            assert_eq!(candidates(bundled(&res, arch).as_deref(), Some("/login/python3"), &fb),
                       vec![PathBuf::from("/login/python3"), PathBuf::from("/fake/homebrew/python3"),
                            PathBuf::from("/fake/local/python3"), PathBuf::from("/fake/usr/python3")]);
        }
        // present → candidate 0 for both arches, ahead of the login shell and the fallbacks
        for (arch, dir) in [("aarch64", "arm64"), ("x86_64", "x86_64")] {
            let bin = res.join("python").join(dir).join("bin");
            std::fs::create_dir_all(&bin).unwrap();
            std::fs::write(bin.join("python3"), "").unwrap();
            let b = bundled(&res, arch);
            assert_eq!(b, Some(bin.join("python3")));
            let c = candidates(b.as_deref(), Some("/login/python3"), &fb);
            assert_eq!(c[0], bin.join("python3"));
            assert_eq!(c[1..], [PathBuf::from("/login/python3"), PathBuf::from("/fake/homebrew/python3"),
                                PathBuf::from("/fake/local/python3"), PathBuf::from("/fake/usr/python3")]);
            // a damaged bundled interpreter (not runnable) is skipped like any failing candidate
            let v = versions(&[("/fake/local/python3", (3, 11))]);
            assert_eq!(resolve(&c, v), Ok(PathBuf::from("/fake/local/python3")));
        }
        std::fs::remove_dir_all(&res).unwrap();
    }

    #[test]
    fn python_version_parsing() {
        assert_eq!(parse_version("Python 3.11.4\n"), Some((3, 11)));
        assert_eq!(parse_version("Python 3.9.6"), Some((3, 9)));
        assert_eq!(parse_version("Python 2.7"), Some((2, 7)));
        assert_eq!(parse_version("xcode-select: note: no developer tools"), None);
    }
}
