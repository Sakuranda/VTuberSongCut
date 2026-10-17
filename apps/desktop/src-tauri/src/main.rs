//! Desktop host for the bundled Python/ML runtime.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use std::{
    fs,
    io::{self, Read, Write},
    net::{Ipv4Addr, SocketAddr, TcpListener, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command},
    sync::{
        atomic::{AtomicU64, Ordering},
        Mutex,
    },
    thread,
    time::{Duration, Instant},
};
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

type StartupError = Box<dyn std::error::Error>;

#[derive(Debug, PartialEq)]
enum ServiceState {
    Missing,
    Ready,
    Unavailable(String),
}

fn is_studio_response(response: &[u8]) -> bool {
    let Some(boundary) = response.windows(4).position(|bytes| bytes == b"\r\n\r\n") else {
        return false;
    };
    let headers = String::from_utf8_lossy(&response[..boundary]);
    if headers
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        != Some("200")
    {
        return false;
    }
    serde_json::from_slice::<serde_json::Value>(&response[boundary + 4..])
        .ok()
        .and_then(|body| {
            body.get("application")
                .and_then(|name| name.as_str())
                .map(str::to_owned)
        })
        .as_deref()
        == Some("VTuberSongCut")
}

fn probe_service(address: SocketAddr) -> ServiceState {
    let mut socket = match TcpStream::connect_timeout(&address, Duration::from_millis(100)) {
        Ok(socket) => socket,
        Err(error) if error.kind() == io::ErrorKind::ConnectionRefused => {
            return ServiceState::Missing
        }
        Err(error) => return ServiceState::Unavailable(error.to_string()),
    };
    // The local FastAPI health response is small and uses Connection: close.
    // Bound reads so an unrelated service cannot hang desktop startup indefinitely.
    let check = (|| -> io::Result<Vec<u8>> {
        socket.set_write_timeout(Some(Duration::from_millis(500)))?;
        write!(
            socket,
            "GET /api/health HTTP/1.1\r\nHost: {address}\r\nConnection: close\r\n\r\n"
        )?;
        let mut response = Vec::new();
        let deadline = Instant::now() + Duration::from_millis(500);
        let mut buffer = [0; 1024];
        loop {
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Err(io::Error::new(
                    io::ErrorKind::TimedOut,
                    "health check timed out",
                ));
            }
            socket.set_read_timeout(Some(remaining))?;
            let count = socket.read(&mut buffer)?;
            if count == 0 {
                break;
            }
            response.extend_from_slice(&buffer[..count]);
            if response.len() > 16 * 1024 {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "health response too large",
                ));
            }
        }
        Ok(response)
    })();
    match check {
        Ok(response) if is_studio_response(&response) => ServiceState::Ready,
        Ok(_) => ServiceState::Unavailable("健康检查未返回 VTuberSongCut 标识".into()),
        Err(error) => ServiceState::Unavailable(error.to_string()),
    }
}

/// Owns only a backend spawned by this shell; a reused service has no child here.
/// Drop also covers startup timeout, window construction failure, and setup errors.
struct Backend {
    child: Mutex<Option<Child>>,
    shutdown: Option<PathBuf>,
    address: SocketAddr,
}

enum SelectedService {
    Reuse(SocketAddr),
    Start(TcpListener),
}

fn select_service(preferred: SocketAddr) -> io::Result<SelectedService> {
    if preferred.ip() != Ipv4Addr::LOCALHOST {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "后端地址必须为 IPv4 loopback",
        ));
    }
    match probe_service(preferred) {
        ServiceState::Ready => return Ok(SelectedService::Reuse(preferred)),
        ServiceState::Missing => match TcpListener::bind(preferred) {
            Ok(reservation) => return Ok(SelectedService::Start(reservation)),
            Err(error) if error.kind() == io::ErrorKind::AddrInUse => {}
            Err(error) => return Err(error),
        },
        ServiceState::Unavailable(_) => {}
    }
    // Ask the OS for a free loopback port. Do not probe or reuse other apps.
    Ok(SelectedService::Start(TcpListener::bind((
        Ipv4Addr::LOCALHOST,
        0,
    ))?))
}

enum AttemptFailure {
    Occupied(String),
    Failed(String),
}

fn backend_command(root: &Path, resources: &Path) -> Result<Command, StartupError> {
    let executable = resources.join("backend").join(if cfg!(windows) {
        "songcut-backend.exe"
    } else {
        "songcut-backend"
    });
    let command = if executable.is_file() {
        Command::new(executable)
    } else if cfg!(debug_assertions) {
        let mut command = Command::new(root.join(if cfg!(windows) {
            ".venv/Scripts/python.exe"
        } else {
            ".venv/bin/python"
        }));
        command.args(["-m", "studio.launcher"]).current_dir(root);
        command
    } else {
        return Err("安装包缺少内置运行环境，请重新下载安装包".into());
    };
    Ok(command)
}

impl Backend {
    fn start(root: &Path, resources: &Path, preferred: SocketAddr) -> Result<Self, StartupError> {
        Self::start_with(preferred, Duration::from_secs(90), || {
            backend_command(root, resources)
        })
    }

    fn start_with<F>(
        preferred: SocketAddr,
        timeout: Duration,
        mut command: F,
    ) -> Result<Self, StartupError>
    where
        F: FnMut() -> Result<Command, StartupError>,
    {
        let mut reservation = match select_service(preferred)? {
            SelectedService::Reuse(address) => {
                return Ok(Self {
                    child: Mutex::new(None),
                    shutdown: None,
                    address,
                })
            }
            SelectedService::Start(reservation) => reservation,
        };
        let deadline = Instant::now() + timeout;
        static NEXT_BACKEND: AtomicU64 = AtomicU64::new(0);
        for attempt in 1..=3 {
            let address = reservation.local_addr()?;
            let shutdown = std::env::temp_dir().join(format!(
                "songcut-shutdown-{}-{}",
                std::process::id(),
                NEXT_BACKEND.fetch_add(1, Ordering::Relaxed)
            ));
            let ready = shutdown.with_extension("ready");
            let _ = fs::remove_file(&shutdown);
            let _ = fs::remove_file(&ready);
            let mut configured = command()?;
            #[cfg(windows)]
            {
                use std::os::windows::process::CommandExt;
                configured.creation_flags(0x08000000); // CREATE_NO_WINDOW
            }
            configured
                .env("SONGCUT_SERVER", "0")
                .env("SONGCUT_HOST", "127.0.0.1")
                .env("SONGCUT_PORT", address.port().to_string())
                .env("SONGCUT_SHUTDOWN_FILE", &shutdown)
                .env("SONGCUT_READY_FILE", &ready);
            // Keep the reservation until immediately before spawn. If another
            // process wins this small gap, only our child is stopped before retry.
            drop(reservation);
            let child = configured
                .spawn()
                .map_err(|error| format!("无法在 {address} 启动内置服务：{error}"))?;
            let backend = Self {
                child: Mutex::new(Some(child)),
                shutdown: Some(shutdown),
                address,
            };
            let failure = match backend.wait_ready(deadline) {
                Ok(()) => return Ok(backend),
                Err(failure) => failure,
            };
            let (occupied, reason) = match failure {
                AttemptFailure::Occupied(reason) => (true, reason),
                AttemptFailure::Failed(reason) => {
                    (probe_service(address) != ServiceState::Missing, reason)
                }
            };
            backend.stop();
            if occupied && attempt < 3 && Instant::now() < deadline {
                reservation = TcpListener::bind((Ipv4Addr::LOCALHOST, 0))?;
                continue;
            }
            return Err(format!(
                "本机服务启动失败（{address}，已尝试 {attempt} 个端口）：{reason}"
            )
            .into());
        }
        unreachable!()
    }

    fn running_pid(&self) -> Result<u32, String> {
        let mut slot = self.child.lock().unwrap_or_else(|error| error.into_inner());
        let child = slot.as_mut().ok_or("启动进程不存在")?;
        if let Some(status) = child.try_wait().map_err(|error| error.to_string())? {
            return Err(format!("启动进程已退出：{status}"));
        }
        Ok(child.id())
    }

    fn ready_matches(&self, pid: u32) -> bool {
        self.shutdown
            .as_ref()
            .and_then(|path| fs::read(path.with_extension("ready")).ok())
            .and_then(|bytes| serde_json::from_slice::<serde_json::Value>(&bytes).ok())
            .is_some_and(|value| {
                value["pid"].as_u64() == Some(pid.into())
                    && value["port"].as_u64() == Some(self.address.port().into())
            })
    }

    fn wait_ready(&self, deadline: Instant) -> Result<(), AttemptFailure> {
        loop {
            let pid = self.running_pid().map_err(AttemptFailure::Failed)?;
            match probe_service(self.address) {
                ServiceState::Ready if self.ready_matches(pid) => {
                    self.running_pid().map_err(AttemptFailure::Failed)?;
                    return Ok(());
                }
                ServiceState::Unavailable(reason) => return Err(AttemptFailure::Occupied(reason)),
                _ => {}
            }
            if Instant::now() >= deadline {
                return Err(AttemptFailure::Failed(
                    "启动超时，未同时收到自身进程就绪信号和 VTuberSongCut 健康检查".into(),
                ));
            }
            thread::sleep(Duration::from_millis(100));
        }
    }

    fn stop(&self) {
        let mut slot = self.child.lock().unwrap_or_else(|error| error.into_inner());
        if let Some(mut child) = slot.take() {
            // The private file asks Uvicorn to run application shutdown, which
            // cancels and reaps owned training workers on every supported OS.
            if let Some(path) = &self.shutdown {
                let _ = fs::write(path, b"shutdown");
                let deadline = Instant::now() + Duration::from_secs(15);
                while Instant::now() < deadline {
                    if matches!(child.try_wait(), Ok(Some(_))) {
                        let _ = fs::remove_file(path);
                        let _ = fs::remove_file(path.with_extension("ready"));
                        return;
                    }
                    thread::sleep(Duration::from_millis(100));
                }
            }
            let _ = child.kill();
            let _ = child.wait();
            if let Some(path) = &self.shutdown {
                let _ = fs::remove_file(path);
                let _ = fs::remove_file(path.with_extension("ready"));
            }
        }
    }
}

impl Drop for Backend {
    fn drop(&mut self) {
        self.stop();
    }
}

fn main() {
    let app = tauri::Builder::default()
        .setup(|app| {
            // Build-time source paths are used only during development.
            let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..");
            let resources = app.path().resource_dir()?;
            let address: SocketAddr = "127.0.0.1:8765".parse()?;
            let backend = Backend::start(&root, &resources, address)?;
            WebviewWindowBuilder::new(
                app,
                "main",
                WebviewUrl::External(format!("http://{}", backend.address).parse()?),
            )
            .title("拾歌 · VTuber SongCut")
            .inner_size(1440.0, 960.0)
            .min_inner_size(900.0, 640.0)
            .build()?;
            app.manage(backend);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("无法启动拾歌工作台");
    app.run(|handle, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            if let Some(state) = handle.try_state::<Backend>() {
                state.stop();
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        process::Stdio,
        sync::{atomic::AtomicBool, Arc},
    };

    fn health_response(application: &str) -> Vec<u8> {
        let body = format!(r#"{{"application":"{application}","version":"0.1.0"}}"#);
        format!(
            "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        )
        .into_bytes()
    }

    struct TestService {
        address: SocketAddr,
        stop: Arc<AtomicBool>,
        thread: Option<thread::JoinHandle<()>>,
    }

    impl TestService {
        fn start(application: &'static str) -> Self {
            let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
            listener.set_nonblocking(true).unwrap();
            let address = listener.local_addr().unwrap();
            let stop = Arc::new(AtomicBool::new(false));
            let stopping = stop.clone();
            let thread = thread::spawn(move || {
                while !stopping.load(Ordering::Relaxed) {
                    if let Ok((mut socket, _)) = listener.accept() {
                        socket
                            .set_read_timeout(Some(Duration::from_secs(1)))
                            .unwrap();
                        let _ = socket.read(&mut [0; 1024]);
                        let _ = socket.write_all(&health_response(application));
                    } else {
                        thread::sleep(Duration::from_millis(10));
                    }
                }
            });
            Self {
                address,
                stop,
                thread: Some(thread),
            }
        }
    }

    impl Drop for TestService {
        fn drop(&mut self) {
            self.stop.store(true, Ordering::Relaxed);
            self.thread.take().unwrap().join().unwrap();
        }
    }

    fn unused_address() -> SocketAddr {
        TcpListener::bind((Ipv4Addr::LOCALHOST, 0))
            .unwrap()
            .local_addr()
            .unwrap()
    }

    fn fixture_command(mode: &str) -> Command {
        let mut command = Command::new(std::env::current_exe().unwrap());
        command
            .args([
                "--exact",
                "tests::backend_process_fixture",
                "--ignored",
                "--nocapture",
            ])
            .env("SONGCUT_TEST_BACKEND", mode)
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        command
    }

    // Run only as a child of the startup tests: this uses real sockets, child
    // environment variables, and the same private readiness/shutdown protocol.
    #[test]
    #[ignore]
    fn backend_process_fixture() {
        let mode = std::env::var("SONGCUT_TEST_BACKEND").unwrap();
        assert_eq!(std::env::var("SONGCUT_HOST").unwrap(), "127.0.0.1");
        assert_eq!(std::env::var("SONGCUT_SERVER").unwrap(), "0");
        let port: u16 = std::env::var("SONGCUT_PORT").unwrap().parse().unwrap();
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, port)).unwrap();
        listener.set_nonblocking(true).unwrap();
        let ready = PathBuf::from(std::env::var("SONGCUT_READY_FILE").unwrap());
        if mode != "other" && mode != "no-ready" {
            let pid = std::process::id();
            let value = serde_json::json!({
                "pid": if mode == "wrong-pid" { pid + 1 } else { pid },
                "port": if mode == "wrong-port" { port.wrapping_add(1) } else { port },
            });
            fs::write(&ready, value.to_string()).unwrap();
        }
        let shutdown = PathBuf::from(std::env::var("SONGCUT_SHUTDOWN_FILE").unwrap());
        let deadline = Instant::now() + Duration::from_secs(15);
        while !shutdown.exists() && Instant::now() < deadline {
            if let Ok((mut socket, _)) = listener.accept() {
                socket
                    .set_read_timeout(Some(Duration::from_secs(1)))
                    .unwrap();
                let _ = socket.read(&mut [0; 1024]);
                let application = if mode == "other" {
                    "OtherApp"
                } else {
                    "VTuberSongCut"
                };
                let _ = socket.write_all(&health_response(application));
            } else {
                thread::sleep(Duration::from_millis(10));
            }
        }
    }

    #[test]
    fn unrelated_preferred_service_survives_fallback_start_and_shutdown() {
        let other = TestService::start("OtherApp");
        let backend = Backend::start_with(other.address, Duration::from_secs(5), || {
            Ok(fixture_command("normal"))
        })
        .unwrap();
        assert_ne!(backend.address, other.address);
        assert_eq!(backend.address.ip(), Ipv4Addr::LOCALHOST);
        assert_eq!(probe_service(backend.address), ServiceState::Ready);
        assert!(matches!(
            probe_service(other.address),
            ServiceState::Unavailable(_)
        ));
        let owned_address = backend.address;
        let shutdown = backend.shutdown.clone().unwrap();
        assert!(shutdown.with_extension("ready").is_file());
        drop(backend);
        assert_eq!(probe_service(owned_address), ServiceState::Missing);
        assert!(matches!(
            probe_service(other.address),
            ServiceState::Unavailable(_)
        ));
        assert!(!shutdown.exists() && !shutdown.with_extension("ready").exists());
    }

    #[test]
    fn verified_preferred_service_is_reused_without_spawning_or_stopping_it() {
        let existing = TestService::start("VTuberSongCut");
        let backend = Backend::start_with(existing.address, Duration::from_secs(1), || {
            panic!("must not spawn")
        })
        .unwrap();
        assert_eq!(backend.address, existing.address);
        assert!(backend.child.lock().unwrap().is_none());
        drop(backend);
        assert_eq!(probe_service(existing.address), ServiceState::Ready);
    }

    #[test]
    fn free_preferred_port_is_kept_and_passed_to_the_owned_child() {
        let preferred = unused_address();
        let backend = Backend::start_with(preferred, Duration::from_secs(5), || {
            Ok(fixture_command("normal"))
        })
        .unwrap();
        assert_eq!(backend.address, preferred);
        assert!(backend.ready_matches(backend.running_pid().unwrap()));
    }

    #[test]
    fn ready_health_alone_cannot_impersonate_owned_child_readiness() {
        for mode in ["no-ready", "wrong-pid", "wrong-port"] {
            let preferred = unused_address();
            let result = Backend::start_with(preferred, Duration::from_millis(400), || {
                Ok(fixture_command(mode))
            });
            assert!(result.err().unwrap().to_string().contains("就绪"));
            assert_eq!(probe_service(preferred), ServiceState::Missing);
        }
    }

    #[test]
    fn startup_collision_retries_another_port_and_bounds_failed_attempts() {
        let preferred = unused_address();
        let mut attempts = 0;
        let backend = Backend::start_with(preferred, Duration::from_secs(10), || {
            attempts += 1;
            Ok(fixture_command(if attempts == 1 {
                "other"
            } else {
                "normal"
            }))
        })
        .unwrap();
        assert_eq!(attempts, 2);
        assert_ne!(backend.address, preferred);
        drop(backend);
        let mut attempts = 0;
        let result = Backend::start_with(unused_address(), Duration::from_secs(10), || {
            attempts += 1;
            Ok(fixture_command("other"))
        });
        assert_eq!(attempts, 3);
        assert!(result
            .err()
            .unwrap()
            .to_string()
            .contains("已尝试 3 个端口"));
    }

    #[test]
    fn health_requires_success_and_exact_application_identity() {
        assert!(is_studio_response(&health_response("VTuberSongCut")));
        assert!(!is_studio_response(&health_response("OtherApp")));
        assert!(!is_studio_response(
            b"HTTP/1.1 500 Error\r\n\r\n{\"application\":\"VTuberSongCut\"}"
        ));
        assert!(!is_studio_response(b"HTTP/1.1 200 OK\r\n\r\nnot json"));
    }

    #[test]
    fn occupied_port_is_reused_only_after_health_check() {
        for application in ["VTuberSongCut", "OtherApp"] {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            let address = listener.local_addr().unwrap();
            let server = thread::spawn(move || {
                let (mut socket, _) = listener.accept().unwrap();
                socket
                    .set_read_timeout(Some(Duration::from_secs(2)))
                    .unwrap();
                let mut request = [0; 1024];
                let count = socket.read(&mut request).unwrap();
                assert!(String::from_utf8_lossy(&request[..count])
                    .starts_with("GET /api/health HTTP/1.1\r\n"));
                socket.write_all(&health_response(application)).unwrap();
            });
            let state = probe_service(address);
            if application == "VTuberSongCut" {
                assert_eq!(state, ServiceState::Ready);
            } else {
                assert!(matches!(state, ServiceState::Unavailable(_)));
            }
            server.join().unwrap();
        }
    }

    #[cfg(unix)]
    #[test]
    fn dropping_owned_backend_stops_child_while_reused_backend_has_no_process() {
        let child = Command::new("/bin/sleep").arg("30").spawn().unwrap();
        let pid = child.id();
        drop(Backend {
            child: Mutex::new(Some(child)),
            shutdown: None,
            address: unused_address(),
        });
        let alive = Command::new("/bin/kill")
            .args(["-0", &pid.to_string()])
            .output()
            .unwrap();
        assert!(
            !alive.status.success(),
            "owned process must be reaped on drop"
        );
        drop(Backend {
            child: Mutex::new(None),
            shutdown: None,
            address: unused_address(),
        });
    }
}
