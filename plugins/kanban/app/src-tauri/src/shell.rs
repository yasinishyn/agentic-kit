//! The Tauri shell (PRD-07): resolve python3, run the bundled `daemon.py --ensure`, open the main window on the daemon
//! origin with the UI token injected there only, lock navigation, own the menu and the terminal commands.
//! The UI token is held in memory and never logged, printed or put in a URL.
use crate::{daemon, nav, pty, python, scope, token};
use serde::Serialize;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tauri::menu::{MenuBuilder, MenuItem, MenuItemBuilder, PredefinedMenuItem, SubmenuBuilder};
use tauri::webview::{NewWindowResponse, PageLoadEvent};
use tauri::{AppHandle, Emitter, Manager, RunEvent, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder, Wry};

/// The app's own status page (bundled `dist/index.html`); it shows `#stopped` or `#error=<message>`.
const STATUS_PAGE: &str = "tauri://localhost/index.html";
const POLL: Duration = Duration::from_secs(3);

#[derive(Clone)]
struct Board {
    port: u16,
    origin: String,
    token: String,
}

#[derive(Default)]
struct Shell {
    home: PathBuf,
    /// The app's resource dir (`Contents/Resources`): the bundled runtime lives under `python/<arch>/`.
    resources: PathBuf,
    scripts: PathBuf,
    /// This build lists the Python runtime in its bundle resources (release config).
    expects_bundled: bool,
    python: Mutex<Option<PathBuf>>,
    board: Mutex<Option<Board>>,
    /// The origin the navigation guard lets into the window (shared with the guard closure).
    origin: Arc<Mutex<Option<String>>>,
    /// (origin, token) baked into the main window's initialization script, if any.
    injected: Mutex<Option<(String, String)>>,
    granted: Mutex<Vec<String>>,
    stop_item: Mutex<Option<MenuItem<Wry>>>,
    busy: Mutex<()>,
    /// A native folder picker is open (one at a time).
    picking: AtomicBool,
}

#[derive(Clone, Serialize)]
struct TermOutput {
    id: u32,
    data: String,
}

#[derive(Clone, Serialize)]
struct TermExit {
    id: u32,
    code: Option<u32>,
}

// ---------------------------------------------------------------- processes

fn run_with_timeout(cmd: &mut Command, timeout: Duration) -> Option<Output> {
    let mut child = cmd.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().ok()?;
    let end = Instant::now() + timeout;
    loop {
        match child.try_wait() {
            Ok(Some(_)) => return child.wait_with_output().ok(),
            Ok(None) if Instant::now() < end => std::thread::sleep(Duration::from_millis(20)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return None;
            }
        }
    }
}

fn login_shell_python() -> Option<String> {
    let out = run_with_timeout(Command::new("/bin/zsh").args(["-lc", "command -v python3"]), Duration::from_secs(8))?;
    out.status.success().then(|| String::from_utf8_lossy(&out.stdout).trim().to_string())
}

/// `<python> [-E] --version` → (major, minor); `-E` for the bundled interpreter so a stray PYTHONHOME cannot break it.
fn python_version(path: &Path, isolated: bool) -> Option<(u32, u32)> {
    if !path.is_file() {
        return None;
    }
    let mut cmd = Command::new(path);
    if isolated {
        cmd.arg("-E");
    }
    let out = run_with_timeout(cmd.arg("--version"), Duration::from_secs(8))?;
    let text = format!("{}{}", String::from_utf8_lossy(&out.stdout), String::from_utf8_lossy(&out.stderr));
    python::parse_version(&text)
}

/// Why the bundled interpreter was not chosen (only reached when resolution failed).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum BundledState {
    Absent,
    NotRunnable,
}

/// True when this build's bundle resources list the Python runtime (merged `tauri.release.conf.json`).
fn expects_bundled_python(resources: Option<&tauri::utils::config::BundleResources>) -> bool {
    use tauri::utils::config::BundleResources;
    let is_python = |s: &String| s.starts_with("resources/python/");
    match resources {
        Some(BundleResources::Map(m)) => m.keys().any(is_python),
        Some(BundleResources::List(l)) => l.iter().any(is_python),
        None => false,
    }
}

/// The error screen text: the bundled path is named first; a release build whose runtime is missing or broken says
/// the app bundle may be damaged.
fn python_error(e: python::PythonError, bundled: Option<&Path>, state: BundledState, expects_bundled: bool) -> String {
    let damaged = match (expects_bundled, state) {
        (false, _) => "",
        (true, BundledState::Absent) => {
            " The bundled Python is missing, so the app bundle may be damaged; reinstall Kanban.app."
        }
        (true, BundledState::NotRunnable) => {
            " The bundled Python does not run, so the app bundle may be damaged; reinstall Kanban.app."
        }
    };
    match e {
        python::PythonError::Missing => format!(
            "Kanban needs Python 3.9 or newer, and none was found ({}login shell PATH, /opt/homebrew/bin, \
             /usr/local/bin, /usr/bin).{damaged} Install Python 3 (e.g. brew install python), then choose Reload.",
            bundled.map(|b| format!("{}, ", b.display())).unwrap_or_default()
        ),
        python::PythonError::TooOld { path, version } => format!(
            "Kanban needs Python 3.9 or newer; {} is {}.{}.{damaged} Install a newer Python 3, then choose Reload.",
            path.display(),
            version.0,
            version.1
        ),
    }
}

/// The bundled runtime (`<resources>/python/<arch>/bin/python3`) first, then the v0.3 order.
fn find_python(resources: &Path, expects_bundled: bool) -> Result<PathBuf, String> {
    let arch = std::env::consts::ARCH;
    let bundled = python::bundled(resources, arch);
    let login = login_shell_python();
    let candidates = python::candidates(bundled.as_deref(), login.as_deref(), &python::FALLBACKS);
    python::resolve(&candidates, |p| python_version(p, Some(p) == bundled.as_deref())).map_err(|e| {
        let state = if bundled.is_some() { BundledState::NotRunnable } else { BundledState::Absent };
        python_error(e, python::bundled_path(resources, arch).as_deref(), state, expects_bundled)
    })
}

fn daemon_command(shell: &Shell, python: &Path, arg: &str) -> Command {
    daemon_command_for(shell, python, arg, std::env::consts::ARCH)
}

/// The bundled interpreter runs `-E -B daemon.py …` (environment PYTHONHOME/PYTHONPATH ignored, no bytecode writes into
/// the bundle); a system interpreter keeps the v0.3 args and gets PYTHONDONTWRITEBYTECODE=1.
fn daemon_command_for(shell: &Shell, python: &Path, arg: &str, arch: &str) -> Command {
    let mut cmd = Command::new(python);
    if python::bundled_path(&shell.resources, arch).as_deref() == Some(python) {
        cmd.args(["-E", "-B"]);
    } else {
        cmd.env("PYTHONDONTWRITEBYTECODE", "1");
    }
    cmd.arg(shell.scripts.join("daemon.py")).arg(arg).env("KANBAN_HOME", &shell.home).current_dir(&shell.home);
    cmd
}

fn python_of(shell: &Shell) -> Result<PathBuf, String> {
    let mut slot = shell.python.lock().map_err(|_| "state poisoned")?;
    if let Some(p) = slot.as_ref() {
        return Ok(p.clone());
    }
    let p = find_python(&shell.resources, shell.expects_bundled)?;
    *slot = Some(p.clone());
    Ok(p)
}

/// `daemon.py --ensure`, then daemon.json + ui.token → the board (errors carry no secrets).
fn ensure_board(shell: &Shell) -> Result<Board, String> {
    let python = python_of(shell)?;
    std::fs::create_dir_all(&shell.home).map_err(|e| format!("cannot create {}: {e}", shell.home.display()))?;
    let out = run_with_timeout(&mut daemon_command(shell, &python, "--ensure"), Duration::from_secs(40))
        .ok_or("the board service did not start within 40 s")?;
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr);
        return Err(format!("The board service did not start: {}", err.trim()));
    }
    let info = std::fs::read_to_string(shell.home.join("daemon.json"))
        .map_err(|e| format!("cannot read daemon.json: {e}"))
        .and_then(|t| daemon::parse_info(&t))?;
    let token = daemon::read_token(&shell.home.join("ui.token"))?;
    Ok(Board { port: info.port, origin: token::daemon_origin(info.port), token })
}

fn open_external(url: &Url) {
    // argv only (no shell); the URL was parsed and its scheme checked by nav::decide
    let _ = Command::new("/usr/bin/open").arg(url.as_str()).stdin(Stdio::null()).spawn();
}

// ---------------------------------------------------------------- window

fn status_url(fragment: &str) -> Url {
    let mut url = Url::parse(STATUS_PAGE).expect("status page URL");
    url.set_fragment(Some(fragment));
    url
}

fn error_fragment(message: &str) -> String {
    let encoded: String = url::form_urlencoded::byte_serialize(message.as_bytes()).collect();
    format!("error={encoded}")
}

fn build_main(app: &AppHandle, url: Url, board: Option<&Board>) -> Result<(), String> {
    let shell = app.state::<Shell>();
    let guard_origin = Arc::clone(&shell.origin);
    let webview_url = if url.scheme() == "tauri" { WebviewUrl::CustomProtocol(url) } else { WebviewUrl::External(url) };
    let handle = app.clone();
    let mut builder = WebviewWindowBuilder::new(app, scope::MAIN, webview_url)
        .title("Kanban")
        .inner_size(1280.0, 820.0)
        .min_inner_size(640.0, 420.0)
        .on_navigation(move |url| {
            let origin = guard_origin.lock().ok().and_then(|o| o.clone());
            match nav::decide(url, origin.as_deref()) {
                nav::Nav::Allow => true,
                nav::Nav::External => {
                    open_external(url);
                    false
                }
                nav::Nav::Block => false,
            }
        })
        .on_new_window(|url, _| {
            if nav::decide(&url, None) == nav::Nav::External {
                open_external(&url);
            }
            NewWindowResponse::Deny
        })
        .on_page_load(move |_, payload| {
            // a (re)loaded page has no terminal views any more: end their processes
            if payload.event() == PageLoadEvent::Started {
                handle.state::<pty::Terminals>().close_all();
            }
        });
    let mut injected = None;
    if let Some(b) = board {
        let script = token::init_script(&b.origin, &b.token).ok_or("not a daemon origin")?;
        builder = builder.initialization_script(script);
        injected = Some((b.origin.clone(), b.token.clone()));
    }
    builder.build().map_err(|e| format!("cannot open the window: {e}"))?;
    *shell.injected.lock().map_err(|_| "state poisoned")? = injected;
    Ok(())
}

/// Show `url` in the main window; (re)create it when the injected token/origin must change.
fn show(app: &AppHandle, url: Url, board: Option<&Board>) -> Result<(), String> {
    let shell = app.state::<Shell>();
    *shell.origin.lock().map_err(|_| "state poisoned")? = board.map(|b| b.origin.clone());
    let want = board.map(|b| (b.origin.clone(), b.token.clone()));
    if let Some(window) = app.get_webview_window(scope::MAIN) {
        let have = shell.injected.lock().map_err(|_| "state poisoned")?.clone();
        if board.is_none() || have == want {
            window.navigate(url).map_err(|e| e.to_string())?;
            let _ = window.set_focus();
            return Ok(());
        }
        let _ = window.destroy();
        let end = Instant::now() + Duration::from_secs(3);
        while app.get_webview_window(scope::MAIN).is_some() && Instant::now() < end {
            std::thread::sleep(Duration::from_millis(20));
        }
    }
    build_main(app, url, board)
}

fn grant(app: &AppHandle, origin: &str, port: u16) -> Result<(), String> {
    let shell = app.state::<Shell>();
    let mut granted = shell.granted.lock().map_err(|_| "state poisoned")?;
    if granted.iter().any(|o| o == origin) {
        return Ok(());
    }
    let mut cap = scope::capability(origin);
    cap["identifier"] = format!("kanban-board-{port}").into();
    app.add_capability(cap.to_string()).map_err(|e| format!("cannot grant the board capability: {e}"))?;
    granted.push(origin.to_string());
    Ok(())
}

/// Startup and "Reload": ensure the daemon, then show the board (or the error page).
fn start(app: &AppHandle) {
    let shell = app.state::<Shell>();
    let Ok(_busy) = shell.busy.try_lock() else { return };
    let result = ensure_board(&shell).and_then(|board| {
        grant(app, &board.origin, board.port)?;
        Ok(board)
    });
    match result {
        Ok(board) => {
            if let Ok(mut slot) = shell.board.lock() {
                *slot = Some(board.clone());
            }
            if let Err(e) = show(app, token::window_url(&board.origin), Some(&board)) {
                eprintln!("kanban: {e}");
            }
        }
        Err(message) => {
            if let Ok(mut slot) = shell.board.lock() {
                *slot = None;
            }
            let _ = show(app, status_url(&error_fragment(&message)), None);
        }
    }
    refresh_stop_item(app);
}

fn stop_board(app: &AppHandle) {
    let shell = app.state::<Shell>();
    let Ok(_busy) = shell.busy.try_lock() else { return };
    let board = shell.board.lock().ok().and_then(|b| b.clone());
    let Some(board) = board else { return };
    if !daemon::stop_enabled(&daemon::http_get(board.port, "/api/runs?live=1", &board.token)) {
        refresh_stop_item(app);
        return;
    }
    let Ok(python) = python_of(&shell) else { return };
    app.state::<pty::Terminals>().close_all();
    let out = run_with_timeout(&mut daemon_command(&shell, &python, "--stop"), Duration::from_secs(20));
    let stopped = out.as_ref().is_some_and(|o| o.status.success());
    if stopped {
        if let Ok(mut slot) = shell.board.lock() {
            *slot = None;
        }
        let _ = show(app, status_url("stopped"), None);
    } else {
        let why = out.map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string()).unwrap_or_default();
        let _ = show(app, status_url(&error_fragment(&format!("The board service was not stopped. {why}"))), None);
    }
    drop(_busy);
    refresh_stop_item(app);
}

fn open_in_browser(app: &AppHandle) {
    let shell = app.state::<Shell>();
    if let Ok(python) = python_of(&shell) {
        let _ = run_with_timeout(&mut daemon_command(&shell, &python, "--open"), Duration::from_secs(40));
    }
}

fn refresh_stop_item(app: &AppHandle) {
    let shell = app.state::<Shell>();
    let board = shell.board.lock().ok().and_then(|b| b.clone());
    let enabled = match board {
        Some(b) => daemon::stop_enabled(&daemon::http_get(b.port, "/api/runs?live=1", &b.token)),
        None => false,
    };
    let item = shell.stop_item.lock().ok().and_then(|i| i.clone());
    if let Some(item) = item {
        let _ = item.set_enabled(enabled);
    }
}

// ---------------------------------------------------------------- terminal commands (main window, daemon origin)

fn allowed_board(app: &AppHandle, window: &WebviewWindow) -> Result<Board, String> {
    let board = app.state::<Shell>().board.lock().ok().and_then(|b| b.clone()).ok_or("the board is not running")?;
    let url = window.url().ok();
    if !scope::terminal_allowed(window.label(), url.as_ref(), &board.origin) {
        return Err("terminal commands are allowed on the board window only".into());
    }
    Ok(board)
}

/// The pty spec for a project terminal: the project id resolves to its registered root through the daemon registry
/// (`GET /api/projects` body); the page never sends a path. Connect Claude uses the "claude-channels" preset.
fn term_spec(projects_body: &str, project_id: &str, preset: &str, shell: &str) -> Result<pty::TermSpec, String> {
    let root = daemon::project_root(projects_body, project_id).ok_or("unknown project")?;
    pty::preset_spec(preset, shell, root)
}

#[tauri::command]
async fn term_open(
    app: AppHandle,
    window: WebviewWindow,
    project_id: String,
    preset: String,
    cols: u16,
    rows: u16,
) -> Result<u32, String> {
    let board = allowed_board(&app, &window)?;
    let (status, body) = daemon::http_get(board.port, "/api/projects", &board.token)?;
    if status != 200 {
        return Err(format!("the project registry answered {status}"));
    }
    let shell = std::env::var("SHELL").unwrap_or_default();
    let spec = term_spec(&body, &project_id, &preset, &shell)?;
    if !spec.cwd.is_dir() {
        return Err(format!("the project folder {} does not exist", spec.cwd.display()));
    }
    let out_app = app.clone();
    let exit_app = app.clone();
    app.state::<pty::Terminals>().open(
        &spec,
        cols,
        rows,
        move |id, data| {
            let _ = out_app.emit_to(scope::MAIN, "term:output", TermOutput { id, data });
        },
        move |id, code| {
            let _ = exit_app.emit_to(scope::MAIN, "term:exit", TermExit { id, code });
        },
    )
}

#[tauri::command]
async fn term_write(app: AppHandle, window: WebviewWindow, id: u32, data: String) -> Result<(), String> {
    allowed_board(&app, &window)?;
    app.state::<pty::Terminals>().write(id, data.as_bytes())
}

#[tauri::command]
async fn term_resize(app: AppHandle, window: WebviewWindow, id: u32, cols: u16, rows: u16) -> Result<(), String> {
    allowed_board(&app, &window)?;
    app.state::<pty::Terminals>().resize(id, cols, rows)
}

#[tauri::command]
async fn term_close(app: AppHandle, window: WebviewWindow, id: u32) -> Result<(), String> {
    allowed_board(&app, &window)?;
    app.state::<pty::Terminals>().close(id)
}

// ---------------------------------------------------------------- folder picker (main window, daemon origin)

/// Held while a native folder picker is open; a second `pick_folder` meanwhile is refused.
struct PickGuard<'a>(&'a AtomicBool);

impl<'a> PickGuard<'a> {
    fn take(flag: &'a AtomicBool) -> Result<Self, String> {
        flag.compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .map(|_| PickGuard(flag))
            .map_err(|_| "a folder picker is already open".to_string())
    }
}

impl Drop for PickGuard<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}

/// Cancel → None; a chosen folder → its absolute path (the daemon still applies its marker rule and the preview).
fn picked_path(path: Option<PathBuf>) -> Result<Option<String>, String> {
    match path {
        None => Ok(None),
        Some(p) if p.is_absolute() => Ok(Some(p.to_string_lossy().into_owned())),
        Some(p) => Err(format!("the folder picker returned a relative path: {}", p.display())),
    }
}

/// Add project → "Choose folder…": the native folder dialog as a sheet on the board window. Async command, so it runs
/// off the main thread; the dialog itself is driven by the plugin on the main thread while this task waits.
#[tauri::command]
async fn pick_folder(app: AppHandle, window: WebviewWindow) -> Result<Option<String>, String> {
    use tauri_plugin_dialog::DialogExt;
    let origin = app.state::<Shell>().board.lock().ok().and_then(|b| b.as_ref().map(|b| b.origin.clone()));
    let url = window.url().ok();
    scope::pick_folder_check(window.label(), url.as_ref(), origin.as_deref())?;
    let shell = app.state::<Shell>();
    let _open = PickGuard::take(&shell.picking)?;
    let dialog = app.dialog().file().set_title("Choose a project folder").set_parent(&window);
    let chosen = tauri::async_runtime::spawn_blocking(move || dialog.blocking_pick_folder())
        .await
        .map_err(|e| format!("the folder picker failed: {e}"))?;
    let path = chosen.map(|p| p.into_path().map_err(|e| format!("the folder picker failed: {e}"))).transpose()?;
    picked_path(path)
}

// ---------------------------------------------------------------- app

fn build_menu(app: &AppHandle) -> tauri::Result<()> {
    let reload = MenuItemBuilder::with_id("reload", "Reload").accelerator("CmdOrCtrl+R").build(app)?;
    let browser = MenuItemBuilder::with_id("open-browser", "Open in Browser").build(app)?;
    let stop = MenuItemBuilder::with_id("stop", "Stop Board Service").enabled(false).build(app)?;
    let quit = MenuItemBuilder::with_id("quit", "Quit Kanban").accelerator("CmdOrCtrl+Q").build(app)?;
    let kanban = SubmenuBuilder::new(app, "Kanban")
        .item(&PredefinedMenuItem::about(app, Some("About Kanban"), None)?)
        .separator()
        .item(&reload)
        .item(&browser)
        .separator()
        .item(&stop)
        .separator()
        .item(&PredefinedMenuItem::hide(app, None)?)
        .item(&quit)
        .build()?;
    // Edit: without it the standard copy/paste shortcuts do not reach the web view on macOS
    let edit = SubmenuBuilder::new(app, "Edit").undo().redo().separator().cut().copy().paste().select_all().build()?;
    let window = SubmenuBuilder::new(app, "Window").minimize().close_window().build()?;
    let menu = MenuBuilder::new(app).items(&[&kanban, &edit, &window]).build()?;
    app.set_menu(menu)?;
    if let Ok(mut slot) = app.state::<Shell>().stop_item.lock() {
        *slot = Some(stop);
    }
    Ok(())
}

fn on_menu(app: &AppHandle, id: &str) {
    let app = app.clone();
    match id {
        "reload" => {
            std::thread::spawn(move || start(&app));
        }
        "open-browser" => {
            std::thread::spawn(move || open_in_browser(&app));
        }
        "stop" => {
            std::thread::spawn(move || stop_board(&app));
        }
        "quit" => {
            app.state::<pty::Terminals>().close_all();
            app.exit(0);
        }
        _ => {}
    }
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(pty::Terminals::default())
        .invoke_handler(tauri::generate_handler![term_open, term_write, term_resize, term_close, pick_folder])
        .on_menu_event(|app, event| on_menu(app, event.id().as_ref()))
        .setup(|app| {
            let env = |k: &str| std::env::var(k).ok();
            let home = daemon::kanban_home(env("KANBAN_HOME"), env("HOME"), env("XDG_DATA_HOME"));
            let resources = app.path().resource_dir()?;
            let scripts = resources.join("kanban").join("scripts");
            let expects_bundled = expects_bundled_python(app.config().bundle.resources.as_ref());
            app.manage(Shell { home, resources, scripts, expects_bundled, ..Default::default() });
            build_menu(app.handle())?;
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                start(&handle);
                loop {
                    std::thread::sleep(POLL);
                    refresh_stop_item(&handle);
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the Kanban app");
    app.run(|app, event| {
        if let RunEvent::Exit = event {
            app.state::<pty::Terminals>().close_all();
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsStr;

    fn shell_at(resources: &str) -> Shell {
        Shell {
            home: PathBuf::from("/fake/home/Kanban"),
            resources: PathBuf::from(resources),
            scripts: PathBuf::from(resources).join("kanban").join("scripts"),
            ..Default::default()
        }
    }

    fn args(cmd: &Command) -> Vec<&OsStr> {
        cmd.get_args().collect()
    }

    fn env_of<'a>(cmd: &'a Command, key: &str) -> Option<Option<&'a OsStr>> {
        cmd.get_envs().find(|(k, _)| *k == OsStr::new(key)).map(|(_, v)| v)
    }

    #[test]
    fn daemon_command_isolated_bundled() {
        let shell = shell_at("/App/Kanban.app/Contents/Resources");
        let daemon = "/App/Kanban.app/Contents/Resources/kanban/scripts/daemon.py";
        for arch in ["aarch64", "x86_64"] {
            // the bundled interpreter: -E (ignore PYTHONHOME/PYTHONPATH) -B (no bytecode writes) before the script
            let bundled = python::bundled_path(&shell.resources, arch).unwrap();
            let cmd = daemon_command_for(&shell, &bundled, "--ensure", arch);
            assert_eq!(cmd.get_program(), bundled.as_os_str());
            assert_eq!(args(&cmd), ["-E", "-B", daemon, "--ensure"]);
            assert_eq!(env_of(&cmd, "KANBAN_HOME"), Some(Some(OsStr::new("/fake/home/Kanban"))));
            assert_eq!(cmd.get_current_dir(), Some(Path::new("/fake/home/Kanban")));
            // a system interpreter: v0.3 args, PYTHONDONTWRITEBYTECODE=1 in the environment
            let cmd = daemon_command_for(&shell, Path::new("/opt/homebrew/bin/python3"), "--stop", arch);
            assert_eq!(args(&cmd), [daemon, "--stop"]);
            assert_eq!(env_of(&cmd, "PYTHONDONTWRITEBYTECODE"), Some(Some(OsStr::new("1"))));
            assert_eq!(env_of(&cmd, "KANBAN_HOME"), Some(Some(OsStr::new("/fake/home/Kanban"))));
        }
        // the other arch's tree is not "the bundled interpreter" for this process
        let other = python::bundled_path(&shell.resources, "x86_64").unwrap();
        assert_eq!(args(&daemon_command_for(&shell, &other, "--open", "aarch64")), [daemon, "--open"]);
    }

    #[test]
    fn python_error_names_bundled_path_first() {
        let b = Path::new("/App/Kanban.app/Contents/Resources/python/arm64/bin/python3");
        let missing = python_error(python::PythonError::Missing, Some(b), BundledState::Absent, true);
        assert!(missing.starts_with("Kanban needs Python 3.9 or newer, and none was found (\
                                     /App/Kanban.app/Contents/Resources/python/arm64/bin/python3, login shell PATH"),
                "{missing}");
        let note = "The bundled Python is missing, so the app bundle may be damaged; reinstall Kanban.app.";
        assert!(missing.contains(note), "{missing}");
        let broken = python_error(python::PythonError::Missing, Some(b), BundledState::NotRunnable, true);
        let note = "The bundled Python does not run, so the app bundle may be damaged; reinstall Kanban.app.";
        assert!(broken.contains(note), "{broken}");
        // a default (from-source) build ships no Python: no damage note, but the path is still named first
        let dev = python_error(python::PythonError::Missing, Some(b), BundledState::Absent, false);
        assert!(!dev.contains("damaged"), "{dev}");
        let first = "(/App/Kanban.app/Contents/Resources/python/arm64/bin/python3, login shell PATH";
        assert!(dev.contains(first), "{dev}");
        assert!(dev.ends_with("Install Python 3 (e.g. brew install python), then choose Reload."), "{dev}");
        let old = python_error(python::PythonError::TooOld { path: PathBuf::from("/usr/bin/python3"), version: (3, 8) },
                               Some(b), BundledState::Absent, true);
        assert!(old.starts_with("Kanban needs Python 3.9 or newer; /usr/bin/python3 is 3.8."), "{old}");
        assert!(old.contains("may be damaged"), "{old}");
    }

    #[test]
    fn connect_claude_uses_preset() {
        // project-level Connect Claude = term_open(project id, "claude-channels"): the id resolves to the registered
        // root through the daemon registry, and the preset runs the channels command through the login shell there
        let body = r#"{"projects": [{"id": "p1", "root": "/work/a"}, {"id": "p2", "root": "/work/b"}]}"#;
        let spec = term_spec(body, "p2", "claude-channels", "/bin/zsh").unwrap();
        assert_eq!(spec.program, "/bin/zsh");
        assert_eq!(spec.args, ["-l", "-i", "-c",
                               "exec claude --dangerously-load-development-channels plugin:kanban@agentic-kit"]);
        assert_eq!(spec.cwd, PathBuf::from("/work/b"));
        let shell = term_spec(body, "p1", "shell", "/opt/homebrew/bin/fish").unwrap();
        assert_eq!((shell.program.as_str(), shell.args.as_slice(), shell.cwd.as_path()),
                   ("/opt/homebrew/bin/fish", ["-l".to_string()].as_slice(), Path::new("/work/a")));
        assert_eq!(term_spec(body, "p3", "claude-channels", "/bin/zsh"), Err("unknown project".to_string()));
        assert_eq!(term_spec(body, "/work/a", "claude-channels", "/bin/zsh"), Err("unknown project".to_string()));
        assert!(term_spec(body, "p1", "rm -rf", "/bin/zsh").unwrap_err().starts_with("unknown terminal preset"));
    }

    #[test]
    fn pick_folder_single_and_absolute() {
        let picking = AtomicBool::new(false);
        let first = PickGuard::take(&picking).unwrap();
        assert_eq!(PickGuard::take(&picking).err(), Some("a folder picker is already open".to_string()));
        drop(first);
        assert!(PickGuard::take(&picking).is_ok(), "released when the dialog closes");
        assert_eq!(picked_path(None), Ok(None));
        assert_eq!(picked_path(Some(PathBuf::from("/Users/dev/My Project"))), Ok(Some("/Users/dev/My Project".into())));
        assert!(picked_path(Some(PathBuf::from("relative/dir"))).is_err());
    }

    #[test]
    fn release_config_expects_bundled_python() {
        use tauri::utils::config::BundleResources;
        use std::collections::HashMap;
        let default = BundleResources::Map(HashMap::from([("../../scripts/*.py".into(), "kanban/scripts/".into())]));
        assert!(!expects_bundled_python(Some(&default)));
        assert!(!expects_bundled_python(None));
        let release = BundleResources::Map(HashMap::from([
            ("../../scripts/*.py".into(), "kanban/scripts/".into()),
            ("resources/python/arm64".into(), "python/arm64".into()),
            ("resources/python/x86_64".into(), "python/x86_64".into()),
        ]));
        assert!(expects_bundled_python(Some(&release)));
        assert!(expects_bundled_python(Some(&BundleResources::List(vec!["resources/python/arm64".into()]))));
    }
}
