//! Embedded terminal (ADR-008): portable-pty sessions keyed by a numeric id. The caller supplies callbacks for output
//! and exit; lib.rs turns them into Tauri events for the main window.
use portable_pty::{native_pty_system, ChildKiller, CommandBuilder, MasterPty, PtySize};
use std::collections::HashMap;
use std::io::{Read, Write};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::{Arc, Mutex};

pub const CLAUDE_CHANNELS: &str = "claude --dangerously-load-development-channels plugin:kanban@agentic-kit";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TermSpec {
    pub program: String,
    pub args: Vec<String>,
    pub cwd: PathBuf,
}

/// Presets: "shell" = the user's login shell; "claude-channels" = channel-enabled claude started via that shell.
pub fn preset_spec(preset: &str, shell: &str, cwd: PathBuf) -> Result<TermSpec, String> {
    let program = if shell.starts_with('/') { shell.to_string() } else { "/bin/zsh".to_string() };
    let args = match preset {
        "shell" => vec!["-l".to_string()],
        "claude-channels" => vec!["-l".into(), "-i".into(), "-c".into(), format!("exec {CLAUDE_CHANNELS}")],
        other => return Err(format!("unknown terminal preset {other:?}")),
    };
    Ok(TermSpec { program, args, cwd })
}

/// Decode PTY bytes as UTF-8 without splitting a multi-byte character across chunks: the incomplete tail stays in
/// `carry` for the next call; invalid bytes become U+FFFD.
pub fn utf8_chunk(carry: &mut Vec<u8>, data: &[u8]) -> String {
    carry.extend_from_slice(data);
    let mut out = String::new();
    let mut rest: &[u8] = carry;
    loop {
        match std::str::from_utf8(rest) {
            Ok(s) => {
                out.push_str(s);
                rest = &[];
                break;
            }
            Err(e) => {
                let (good, bad) = rest.split_at(e.valid_up_to());
                out.push_str(std::str::from_utf8(good).unwrap_or_default());
                match e.error_len() {
                    Some(n) => {
                        out.push('\u{fffd}');
                        rest = &bad[n..];
                    }
                    None => {
                        rest = bad; // incomplete sequence at the end: keep it for the next chunk
                        break;
                    }
                }
            }
        }
    }
    let tail = rest.to_vec();
    *carry = tail;
    out
}

struct Session {
    master: Box<dyn MasterPty + Send>,
    writer: Box<dyn Write + Send>,
    killer: Box<dyn ChildKiller + Send + Sync>,
}

#[derive(Default)]
pub struct Terminals {
    next: AtomicU32,
    sessions: Arc<Mutex<HashMap<u32, Session>>>,
}

fn size(cols: u16, rows: u16) -> PtySize {
    PtySize { rows: rows.clamp(2, 500), cols: cols.clamp(2, 1000), pixel_width: 0, pixel_height: 0 }
}

impl Terminals {
    pub fn open(
        &self,
        spec: &TermSpec,
        cols: u16,
        rows: u16,
        on_output: impl Fn(u32, String) + Send + 'static,
        on_exit: impl FnOnce(u32, Option<u32>) + Send + 'static,
    ) -> Result<u32, String> {
        let pair = native_pty_system().openpty(size(cols, rows)).map_err(|e| e.to_string())?;
        let mut cmd = CommandBuilder::new(&spec.program);
        cmd.args(&spec.args);
        cmd.cwd(&spec.cwd);
        cmd.env("TERM", "xterm-256color");
        cmd.env("COLORTERM", "truecolor");
        if std::env::var_os("LANG").is_none() {
            cmd.env("LANG", "en_US.UTF-8");
        }
        let mut child = pair.slave.spawn_command(cmd).map_err(|e| format!("cannot start {}: {e}", spec.program))?;
        drop(pair.slave);
        let killer = child.clone_killer();
        let mut reader = pair.master.try_clone_reader().map_err(|e| e.to_string())?;
        let writer = pair.master.take_writer().map_err(|e| e.to_string())?;
        let id = self.next.fetch_add(1, Ordering::SeqCst) + 1;
        self.sessions.lock().map_err(|_| "terminal registry poisoned")?.insert(
            id,
            Session { master: pair.master, writer, killer },
        );
        let sessions = Arc::clone(&self.sessions);
        std::thread::spawn(move || {
            let mut buf = [0u8; 8192];
            let mut carry = Vec::new();
            loop {
                match reader.read(&mut buf) {
                    Ok(0) | Err(_) => break,
                    Ok(n) => {
                        let text = utf8_chunk(&mut carry, &buf[..n]);
                        if !text.is_empty() {
                            on_output(id, text);
                        }
                    }
                }
            }
            let code = child.wait().ok().map(|s| s.exit_code());
            if let Ok(mut map) = sessions.lock() {
                map.remove(&id);
            }
            on_exit(id, code);
        });
        Ok(id)
    }

    fn with<T>(&self, id: u32, f: impl FnOnce(&mut Session) -> Result<T, String>) -> Result<T, String> {
        let mut map = self.sessions.lock().map_err(|_| "terminal registry poisoned")?;
        let session = map.get_mut(&id).ok_or_else(|| format!("no terminal {id}"))?;
        f(session)
    }

    pub fn write(&self, id: u32, data: &[u8]) -> Result<(), String> {
        self.with(id, |s| {
            s.writer.write_all(data).and_then(|_| s.writer.flush()).map_err(|e| e.to_string())
        })
    }

    pub fn resize(&self, id: u32, cols: u16, rows: u16) -> Result<(), String> {
        self.with(id, |s| s.master.resize(size(cols, rows)).map_err(|e| e.to_string()))
    }

    pub fn close(&self, id: u32) -> Result<(), String> {
        let session = self
            .sessions
            .lock()
            .map_err(|_| "terminal registry poisoned")?
            .remove(&id)
            .ok_or_else(|| format!("no terminal {id}"))?;
        let mut session = session;
        let _ = session.killer.kill();
        Ok(())
    }

    pub fn close_all(&self) {
        let ids: Vec<u32> = match self.sessions.lock() {
            Ok(map) => map.keys().copied().collect(),
            Err(_) => return,
        };
        for id in ids {
            let _ = self.close(id);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc;
    use std::time::{Duration, Instant};

    #[test]
    fn term_roundtrip() {
        let terms = Terminals::default();
        let spec = TermSpec { program: "sh".into(), args: vec!["-c".into(), "echo hi".into()],
                              cwd: std::env::temp_dir() };
        let (tx, rx) = mpsc::channel::<String>();
        let (etx, erx) = mpsc::channel::<(u32, Option<u32>)>();
        let id = terms.open(&spec, 80, 24, move |_, s| { let _ = tx.send(s); },
                            move |id, code| { let _ = etx.send((id, code)); }).expect("spawn");
        let mut out = String::new();
        let end = Instant::now() + Duration::from_secs(10);
        while !out.contains("hi") && Instant::now() < end {
            if let Ok(s) = rx.recv_timeout(Duration::from_millis(100)) { out.push_str(&s); }
        }
        assert!(out.contains("hi"), "output was {out:?}");
        let (eid, code) = erx.recv_timeout(Duration::from_secs(10)).expect("exit event");
        assert_eq!((eid, code), (id, Some(0)));
        assert!(terms.write(9999, b"x").is_err());
        let _ = terms.close(id);
    }

    #[test]
    fn term_write_resize_close_interactive() {
        let terms = Terminals::default();
        let spec = TermSpec { program: "sh".into(), args: vec![], cwd: std::env::temp_dir() };
        let (tx, rx) = mpsc::channel::<String>();
        let id = terms.open(&spec, 80, 24, move |_, s| { let _ = tx.send(s); }, |_, _| {}).expect("spawn");
        terms.resize(id, 100, 30).expect("resize");
        terms.write(id, b"stty size; echo mark-$((40+2))\n").expect("write");
        let mut out = String::new();
        let end = Instant::now() + Duration::from_secs(10);
        while !out.contains("mark-42") && Instant::now() < end {
            if let Ok(s) = rx.recv_timeout(Duration::from_millis(100)) { out.push_str(&s); }
        }
        assert!(out.contains("mark-42") && out.contains("30 100"), "output was {out:?}");
        terms.close(id).expect("close");
        assert!(terms.write(id, b"x").is_err());
    }

    #[test]
    fn presets() {
        let cwd = PathBuf::from("/tmp/p");
        assert_eq!(preset_spec("shell", "/bin/zsh", cwd.clone()),
                   Ok(TermSpec { program: "/bin/zsh".into(), args: vec!["-l".into()], cwd: cwd.clone() }));
        assert_eq!(preset_spec("claude-channels", "/bin/zsh", cwd.clone()),
                   Ok(TermSpec { program: "/bin/zsh".into(),
                                 args: vec!["-l".into(), "-i".into(), "-c".into(),
                                            "exec claude --dangerously-load-development-channels plugin:kanban@agentic-kit".into()],
                                 cwd: cwd.clone() }));
        assert!(preset_spec("rm -rf /", "/bin/zsh", cwd.clone()).is_err());
        // a non-absolute $SHELL falls back to /bin/zsh
        assert_eq!(preset_spec("shell", "zsh", cwd.clone()).unwrap().program, "/bin/zsh");
        assert_eq!(preset_spec("shell", "", cwd).unwrap().program, "/bin/zsh");
    }

    #[test]
    fn utf8_chunks_keep_split_characters() {
        let mut carry = Vec::new();
        let bytes = "é✓".as_bytes(); // c3 a9 e2 9c 93
        assert_eq!(utf8_chunk(&mut carry, &bytes[..1]), "");
        assert_eq!(utf8_chunk(&mut carry, &bytes[1..3]), "é");
        assert_eq!(utf8_chunk(&mut carry, &bytes[3..]), "✓");
        assert!(carry.is_empty());
        assert_eq!(utf8_chunk(&mut carry, b"a\xffb"), "a\u{fffd}b");
    }
}
