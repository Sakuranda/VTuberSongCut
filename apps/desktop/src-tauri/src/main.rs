//! Desktop host for the bundled Python/ML runtime.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
use std::{
    fs,
    io::{self, Read, Write},
    net::{SocketAddr, TcpStream},
    path::{Path, PathBuf},
    process::{Child, Command},
    sync::Mutex,
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
struct Backend(Mutex<Option<Child>>, Option<PathBuf>);

impl Backend {
    fn start(root: &Path, resources: &Path, address: SocketAddr) -> Result<Self, StartupError> {
        match probe_service(address) {
            ServiceState::Ready => return Ok(Self(Mutex::new(None), None)),
            ServiceState::Unavailable(reason) => {
                return Err(format!(
                    "端口 8765 已被其他服务占用或无法验证，请先关闭该服务：{reason}"
                )
                .into());
            }
            ServiceState::Missing => {}
        }
        let executable = resources.join("backend").join(if cfg!(windows) {
            "songcut-backend.exe"
        } else {
            "songcut-backend"
        });
        let shutdown =
            std::env::temp_dir().join(format!("songcut-shutdown-{}", std::process::id()));
        let _ = fs::remove_file(&shutdown);
        let mut command = if executable.is_file() {
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
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000); // CREATE_NO_WINDOW
        }
        let child = command
            .env("SONGCUT_SERVER", "0")
            .env("SONGCUT_PORT", "8765")
            .env("SONGCUT_SHUTDOWN_FILE", &shutdown)
            .spawn()?;
        let backend = Self(Mutex::new(Some(child)), Some(shutdown));
        let deadline = Instant::now() + Duration::from_secs(90);
        loop {
            {
                let mut slot = backend.0.lock().unwrap_or_else(|error| error.into_inner());
                if let Some(status) = slot.as_mut().expect("owned backend").try_wait()? {
                    return Err(format!("本机服务启动失败：{status}").into());
                }
            }
            if probe_service(address) == ServiceState::Ready {
                return Ok(backend);
            }
            if Instant::now() >= deadline {
                return Err("本机服务启动超时，未通过 VTuberSongCut 健康检查".into());
            }
            thread::sleep(Duration::from_millis(100));
        }
    }

    fn stop(&self) {
        let mut slot = self.0.lock().unwrap_or_else(|error| error.into_inner());
        if let Some(mut child) = slot.take() {
            // The private file asks Uvicorn to run application shutdown, which
            // cancels and reaps owned training workers on every supported OS.
            if let Some(path) = &self.1 {
                let _ = fs::write(path, b"shutdown");
                let deadline = Instant::now() + Duration::from_secs(15);
                while Instant::now() < deadline {
                    if matches!(child.try_wait(), Ok(Some(_))) {
                        let _ = fs::remove_file(path);
                        return;
                    }
                    thread::sleep(Duration::from_millis(100));
                }
            }
            let _ = child.kill();
            let _ = child.wait();
            if let Some(path) = &self.1 {
                let _ = fs::remove_file(path);
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
                WebviewUrl::External("http://127.0.0.1:8765".parse()?),
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
    use std::net::TcpListener;

    fn health_response(application: &str) -> Vec<u8> {
        let body = format!(r#"{{"application":"{application}","version":"0.1.0"}}"#);
        format!(
            "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        )
        .into_bytes()
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
        drop(Backend(Mutex::new(Some(child)), None));
        let alive = Command::new("/bin/kill")
            .args(["-0", &pid.to_string()])
            .output()
            .unwrap();
        assert!(
            !alive.status.success(),
            "owned process must be reaped on drop"
        );
        drop(Backend(Mutex::new(None), None));
    }
}
