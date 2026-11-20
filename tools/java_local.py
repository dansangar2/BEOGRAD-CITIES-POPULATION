"""Supervise Django plus Java (default), or Django alone; build/roll back Java.

Startup restarts both owned servers and builds changed Java sources when selected.
It uses an existing SQL file; it never imports or migrates.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
from ctypes import wintypes
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import tomllib
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend-java"
RUNTIME = Path(os.environ.get("BEOGRAD_RUNTIME_DIR", BACKEND / ".runtime")).resolve()
CONFIG = Path(os.environ.get("BEOGRAD_CONFIG", BACKEND / "config/local.json")).resolve()
WRAPPER_HASH = "4e2fbf6554bc8a4702cdfdd3bef464f423393d784ddbb037216320ce55d5e4e1"
BACKEND_MODE = b"java-only-v1"


class LocalError(RuntimeError):
    pass


def selected_backend():
    """One explicit HTTP backend; missing configuration defaults to Java."""
    path = ROOT / "backend.toml"
    config = tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    backend = os.environ.get("BEOGRAD_BACKEND", config.get("backend", "java"))
    if backend not in ("java", "python"):
        raise LocalError('backend must be "java" or "python" in backend.toml / BEOGRAD_BACKEND')
    return backend


def python_environment():
    env = os.environ.copy()
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "BEOGRAD_BACKEND": "python",
                "DJANGO_SETTINGS_MODULE": "ciudades_del_mundo.local_settings"})
    if not env.get("BEOGRAD_DJANGO_SECRET_KEY"):
        secret = ROOT / ".web_django_secret"
        if not secret.exists():
            with secret.open("x", encoding="utf-8") as stream:
                stream.write(secrets.token_urlsafe(48))
    return env


def configuration():
    config = json.loads(CONFIG.read_text(encoding="utf-8")) if CONFIG.is_file() else {}
    for key, env in (("java_home", "BEOGRAD_JAVA_HOME"), ("database", "BEOGRAD_DATABASE"), ("media_root", "BEOGRAD_MEDIA_ROOT"), ("startup_timeout", "BEOGRAD_STARTUP_TIMEOUT")):
        if os.environ.get(env):
            config[key] = os.environ[env]
    return config


def java_home(config):
    candidate = config.get("java_home") or os.environ.get("JAVA_HOME")
    if not candidate:
        javac = shutil.which("javac")
        if javac:
            candidate = str(Path(javac).resolve().parents[1])
    if not candidate:
        raise LocalError("JDK 25 required (JRE is insufficient). Set BEOGRAD_JAVA_HOME or JAVA_HOME; https://adoptium.net/temurin/releases/?version=25 . No global settings were changed.")
    home = Path(candidate).resolve()
    for executable in ("java", "javac"):
        path = home / "bin" / (executable + ".exe")
        if not path.is_file():
            raise LocalError(f"Missing {executable}.exe in JDK: {home}")
        result = subprocess.run([str(path), "-version"], capture_output=True, text=True, timeout=15)
        output = result.stdout + result.stderr
        import re
        if result.returncode or not re.search(r'(?:version\s+"|javac\s+)25(?:\.|\")', output):
            raise LocalError(f"{executable} must be JDK 25: {home}")
    return home


def python_exe():
    explicit = os.environ.get("BEOGRAD_PYTHON")
    # The supervisor uses only the standard library; no Django environment.
    return str(Path(explicit).resolve()) if explicit else sys.executable


def checked(args, **kwargs):
    result = subprocess.run(args, **kwargs)
    if result.returncode:
        raise LocalError(f"Command failed, exit code {result.returncode}")


def wrapper_check():
    jar = BACKEND / ".mvn/wrapper/maven-wrapper.jar"
    if not jar.is_file() or hashlib.sha256(jar.read_bytes()).hexdigest() != WRAPPER_HASH:
        raise LocalError("Maven Wrapper JAR integrity check failed; restore the checked-in wrapper")


def environment(config):
    env = os.environ.copy()
    env["JAVA_HOME"] = str(java_home(config))
    env["MAVEN_USER_HOME"] = os.environ.get("BEOGRAD_MAVEN_USER_HOME", str(BACKEND / ".maven"))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Java runs independently of Django and the retired hybrid routing flag.
    for key in tuple(env):
        if key.startswith(("DJANGO_", "BEOGRAD_DJANGO_")) or key == "BEOGRAD_JAVA_ENABLED":
            env.pop(key, None)
    return env


class InstanceLock:
    """OS ownership, not a PID file or JVM mutex. File persists after release."""
    def __init__(self, path):
        import msvcrt
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("a+b")
        if self.file.seek(0, 2) == 0:
            self.file.write(b"\0")
            self.file.flush()
        self.file.seek(0)
        try:
            msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            self.file.close()
            raise LocalError(f"Another local operation owns {path.name}") from exc

    def __enter__(self):
        return self

    def __exit__(self, *args):
        import msvcrt
        self.file.seek(0)
        msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        self.file.close()


def prepare():
    config = configuration()
    home = java_home(config)
    wrapper_check()
    database = Path(config.get("database") or ROOT / "db.sqlite3")
    if not database.is_absolute() or not database.is_file():
        raise LocalError("Preparation needs an explicit existing SQLite file; set BEOGRAD_DATABASE. No database was created.")
    if not CONFIG.exists():
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        config.update({"java_home": str(home), "database": str(database), "media_root": str(config.get("media_root") or ROOT / "media"), "startup_timeout": config.get("startup_timeout", 45)})
        CONFIG.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Prepared standalone Java API. Configuration: {CONFIG}. No Django dependencies installed. First build needs Maven Central access.")


def verify_artifact(path):
    with zipfile.ZipFile(path) as artifact:
        if "org.springframework.boot.loader.launch.JarLauncher" not in artifact.read("META-INF/MANIFEST.MF").decode():
            raise LocalError("Build did not produce an executable Spring Boot JAR")
        try:
            mode = artifact.read("BOOT-INF/classes/backend-mode.txt")
        except KeyError as exc:
            raise LocalError("Artifact predates standalone Java mode; rebuild with levantar_app.bat build") from exc
        if mode.strip() != BACKEND_MODE:
            raise LocalError("Artifact is not compatible with standalone Java mode")


def source_fingerprint():
    """Hash build inputs only; runtime state and generated output are excluded."""
    paths = [BACKEND / "pom.xml", BACKEND / "mvnw.cmd"]
    for directory in (BACKEND / "src", BACKEND / ".mvn"):
        paths.extend(path for path in directory.rglob("*") if path.is_file())
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(BACKEND).as_posix().encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def build():
    config = configuration()
    env = environment(config)
    wrapper_check()
    with InstanceLock(RUNTIME / "owner.lock"):
        fingerprint = source_fingerprint()
        # Explicit call, fixed arguments, quoted paths. No shell input comes from HTTP.
        repository = Path(os.environ.get("BEOGRAD_MAVEN_REPOSITORY", BACKEND / ".maven/repository")).resolve()
        if any(c in str(ROOT) + str(repository) for c in ('%', '"', '\r', '\n')):
            raise LocalError("Maven batch path cannot contain %, quotes or line breaks")
        command = f'call "{BACKEND / "mvnw.cmd"}" -B -ntp "-Dmaven.repo.local={repository}" clean verify'
        checked(f'cmd.exe /d /s /v:off /c "{command}"', cwd=BACKEND, env=env)
        artifact = BACKEND / "target/beograd-api.jar"
        verify_artifact(artifact)
        if source_fingerprint() != fingerprint:
            raise LocalError("Sources changed during the build; run levantar_app.bat again")
        staged = RUNTIME / "verified-build.jar"
        shutil.copyfile(artifact, staged)
        current = RUNTIME / "current.jar"
        if current.is_file():
            shutil.copyfile(current, RUNTIME / "previous.jar")
            shutil.copyfile(RUNTIME / "current.sha256", RUNTIME / "previous.sha256")
        os.replace(staged, current)
        (RUNTIME / "current.sha256").write_text(hashlib.sha256(current.read_bytes()).hexdigest(), encoding="ascii")
        (RUNTIME / "build.sources.sha256").write_text(fingerprint, encoding="ascii")
    print(f"Verified and activated {current}. Previous artifact retained; no data or schema changed.")


def rollback():
    with InstanceLock(RUNTIME / "owner.lock"):
        previous = RUNTIME / "previous.jar"
        if not previous.is_file():
            raise LocalError("No previous verified artifact to restore")
        expected = (RUNTIME / "previous.sha256").read_text(encoding="ascii").strip()
        if hashlib.sha256(previous.read_bytes()).hexdigest() != expected:
            raise LocalError("Previous artifact checksum mismatch")
        verify_artifact(previous)
        fingerprint = source_fingerprint()
        staged = RUNTIME / "rollback.jar"
        shutil.copyfile(previous, staged)
        os.replace(staged, RUNTIME / "current.jar")
        (RUNTIME / "current.sha256").write_text(expected, encoding="ascii")
        # Honor an explicit rollback until the user edits the sources again.
        (RUNTIME / "build.sources.sha256").write_text(fingerprint, encoding="ascii")
    print("Previous JAR restored. Data and schema are unchanged; artifact rollback does not undo SQL writes.")


class WindowsJob:
    """Kill only our process tree if supervisor exits or startup fails."""
    def __init__(self):
        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64), ("flags", wintypes.DWORD),
                        ("min_working", ctypes.c_size_t), ("max_working", ctypes.c_size_t), ("active", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
        class Counters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]
        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", Counters), ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, process):
        if not self.kernel.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(process._handle))):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def child(command):
    # The supervisor assigns this waiting wrapper to its job BEFORE any child
    # can spawn. Descendants inherit ownership; no assignment race/orphans.
    if sys.stdin.readline().strip() != "go":
        return 2
    worker = subprocess.Popen(command, cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW)
    def control():
        for line in sys.stdin:
            if line.strip() == "stop" and worker.poll() is None:
                worker.terminate()
                return
    threading.Thread(target=control, daemon=True).start()
    return worker.wait()


def parse_port(value):
    try:
        port = int(value)
    except (ValueError, TypeError) as exc:
        raise LocalError("Port must be an integer from 1 to 65535") from exc
    if not 1 <= port <= 65535:
        raise LocalError("Port must be 1..65535")
    return port


def check_port(value):
    port = parse_port(value)
    with socket.socket() as sock:
        if os.name == "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError as exc:
            raise LocalError(f"Port {port} unavailable; no existing process was stopped") from exc
    return port


def wait_ready(url, processes, timeout):
    deadline = time.monotonic() + timeout
    opener = build_opener(ProxyHandler({}))
    while time.monotonic() < deadline:
        if any(p.poll() is not None for p in processes):
            raise LocalError("A dependency exited during startup; inspect the isolated runtime log")
        try:
            with opener.open(url, timeout=1) as response:
                if response.status == 200 and json.load(response).get("status") == "ready":
                    return
        except (URLError, OSError, ValueError):
            pass
        time.sleep(0.1)
    raise LocalError(f"Readiness timeout after {timeout}s")


def start(port, backend="java"):
    config = configuration()
    env = environment(config) if backend == "java" else python_environment()
    database = Path(config.get("database", ""))
    if not database.is_absolute() or not database.is_file():
        raise LocalError("Configure an absolute existing SQLite file in BEOGRAD_DATABASE or local.json; no empty database was created")
    database = database.resolve()
    if backend == "java":
        artifact = RUNTIME / "current.jar"
        if not artifact.is_file():
            raise LocalError("Missing verified artifact; run levantar_app.bat build first")
        if hashlib.sha256(artifact.read_bytes()).hexdigest() != (RUNTIME / "current.sha256").read_text(encoding="ascii").strip():
            raise LocalError("Active JAR checksum mismatch")
        verify_artifact(artifact)
    public = check_port(port)
    internal = check_port(public + 1) if backend == "java" else public
    token = secrets.token_hex(16)
    env.update({"BEOGRAD_DATABASE": str(database), "BEOGRAD_JAVA_PORT": str(internal),
                "BEOGRAD_MEDIA_ROOT": str(config.get("media_root") or ROOT / "media"), "BEOGRAD_SHUTDOWN_TOKEN": token})
    database_lock = database.parent / (".web_java_owner_" + hashlib.sha256(str(database).casefold().encode()).hexdigest()[:16] + ".lock")
    with InstanceLock(RUNTIME / "owner.lock"), InstanceLock(database_lock):
        job = WindowsJob()
        processes = []
        logger = logging.getLogger("beograd.local")
        handler = RotatingFileHandler(RUNTIME / "process.log", maxBytes=2*1024*1024, backupCount=2, encoding="utf-8")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        nonce = secrets.token_hex(16)
        stop_file = RUNTIME / ("stop-" + nonce)
        record = RUNTIME / "owner.json"
        def spawn(command, child_env=None):
            p = subprocess.Popen([python_exe(), "-B", str(Path(__file__).resolve()), "_child", *command], cwd=ROOT, env=child_env or env,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                 creationflags=subprocess.CREATE_NO_WINDOW)
            processes.append(p)
            job.assign(p)
            p.stdin.write("go\n")
            p.stdin.flush()
            def pump():
                for line in p.stdout:
                    logger.info("pid=%s %s", p.pid, line.rstrip())
            threading.Thread(target=pump, daemon=True).start()
        try:
            if backend == "java":
                spawn([str(Path(env["JAVA_HOME"]) / "bin/java.exe"), "--enable-native-access=ALL-UNNAMED", "-jar", str(artifact)])
                timeout = max(1, min(120, int(config.get("startup_timeout", 45))))
                wait_ready(f"http://127.0.0.1:{internal}/internal/ready", processes, timeout)
                django_env = python_environment()
                django_env.update({key: env[key] for key in ("BEOGRAD_DATABASE", "BEOGRAD_MEDIA_ROOT", "BEOGRAD_JAVA_PORT")})
                django_env["BEOGRAD_JAVA_ENABLED"] = "1"
                spawn([python_exe(), "-B", str(ROOT / "manage.py"), "runserver",
                       f"127.0.0.1:{public}", "--noreload", "--insecure"], django_env)
            else:
                spawn([python_exe(), "-B", str(ROOT / "manage.py"), "runserver",
                       f"127.0.0.1:{public}", "--noreload", "--insecure"])
            timeout = max(1, min(120, int(config.get("startup_timeout", 45))))
            wait_ready(f"http://127.0.0.1:{public}/internal/ready", processes, timeout)
            record.write_text(json.dumps({"backend": backend, "pid": os.getpid(), "nonce": nonce, "port": public, "internal_port": internal, "children": [p.pid for p in processes]}), encoding="utf-8")
            print(f"Django ready: http://127.0.0.1:{public}/", flush=True)
            print(f"{backend.capitalize()} API ready: http://127.0.0.1:{public}/api/countries/", flush=True)
            if backend == "java":
                print(f"Java direct API: http://127.0.0.1:{internal}/api/countries/", flush=True)
            print("Close this window or press Ctrl+C to stop both. Reopen levantar_app.bat to restart; changed Java sources are rebuilt automatically.", flush=True)
            while not stop_file.exists():
                if any(p.poll() is not None for p in processes):
                    raise LocalError("A dependency exited; stopping the owned process tree")
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass
        finally:
            java_stopping = False
            if backend == "java" and processes and processes[0].poll() is None:
                try:
                    request = Request(f"http://127.0.0.1:{internal}/internal/shutdown", method="POST", headers={"X-Beograd-Shutdown-Token": token})
                    with build_opener(ProxyHandler({})).open(request, timeout=2) as response:
                        java_stopping = response.status == 202
                except (URLError, OSError, ValueError):
                    pass
            for index, p in enumerate(processes):
                if index == 0 and java_stopping:
                    continue
                if p.poll() is None:
                    with contextlib.suppress(OSError):
                        p.stdin.write("stop\n")
                        p.stdin.flush()
            deadline = time.monotonic() + 5
            while any(p.poll() is None for p in processes) and time.monotonic() < deadline:
                time.sleep(0.05)
            job.close()
            for p in processes:
                with contextlib.suppress(subprocess.TimeoutExpired):
                    p.wait(timeout=3)
                for stream in (p.stdin, p.stdout):
                    if stream:
                        with contextlib.suppress(OSError):
                            stream.close()
            record.unlink(missing_ok=True)
            stop_file.unlink(missing_ok=True)
            handler.close()
            logger.removeHandler(handler)


def stop():
    record = RUNTIME / "owner.json"
    if not record.is_file():
        print("No ready local instance is registered")
        return
    try:
        lease = InstanceLock(RUNTIME / "owner.lock")
    except LocalError:
        lease = None
    if lease is not None:
        # A crashed supervisor has already lost its Windows job handle. Never
        # terminate a process by a stale/reused PID from this diagnostic record.
        with lease:
            record.unlink(missing_ok=True)
        print("No live owner remains; stale registration cleared")
        return
    info = json.loads(record.read_text(encoding="utf-8"))
    nonce = info.get("nonce", "")
    import re
    if not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise LocalError("Invalid owner record")
    (RUNTIME / ("stop-" + nonce)).touch()
    deadline = time.monotonic() + 12
    while record.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    if record.exists():
        raise LocalError("Owner did not confirm stop; no unrelated process was terminated")
    # The record is removed just before the supervisor releases its OS lock.
    # Wait for that release before allowing a rebuild or another startup.
    while True:
        try:
            lease = InstanceLock(RUNTIME / "owner.lock")
        except LocalError:
            if time.monotonic() >= deadline:
                raise LocalError("Owner did not release its lock after stopping")
            time.sleep(0.05)
        else:
            with lease:
                break
    print("Owned local instance stopped")


def restart(port):
    port = parse_port(port)
    selected_backend()
    stop()
    backend = selected_backend()
    if backend == "python":
        return start(port, backend)
    fingerprint = source_fingerprint()
    stamp = RUNTIME / "build.sources.sha256"
    if not (RUNTIME / "current.jar").is_file() or not stamp.is_file() or stamp.read_text(encoding="ascii").strip() != fingerprint:
        print("Java sources changed or no verified build exists; building before startup.", flush=True)
        build()
    start(port)


def main():
    if sys.version_info < (3, 13):
        raise LocalError("Python 3.13 or later is required for the standard-library Windows process supervisor")
    if len(sys.argv) > 1 and sys.argv[1] == "_child":
        return child(sys.argv[2:])
    parser = argparse.ArgumentParser(prog="levantar_app.bat", description=__doc__)
    parser.add_argument("action", nargs="?", default="start", choices=("prepare", "build", "start", "stop", "rollback"))
    parser.add_argument("port", nargs="?")
    arguments = sys.argv[1:]
    if arguments and arguments[0].isdecimal():
        arguments = ["start", *arguments]
    args = parser.parse_args(arguments)
    if args.action != "start" and args.port is not None:
        parser.error("Only start accepts a port")
    if os.name != "nt":
        raise LocalError("These deployment scripts target Windows")
    {"prepare": prepare, "build": build, "start": lambda: restart(args.port or "8000"), "stop": stop, "rollback": rollback}[args.action]()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (LocalError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"Local deployment failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
