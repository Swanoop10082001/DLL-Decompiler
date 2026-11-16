#!/usr/bin/env python3
"""
DLL Decompiler – Portable Edition (Cross-Platform Auto-Download)
================================================================

v4 – Windows + Linux + macOS
    • Auto-downloads .NET SDK/runtime + ILSpy on every OS
    • PowerShell detection (powershell.exe / powershell / pwsh)
    • Linux chmod for scripts, dotnet, and ilspycmd shim
    • Direct binary fallback if bash/curl/wget missing
    • Windows long-path safe (\\\\?\\ prefix)
    • Thread-safe ILSpy verification
    • Decompiles ALL DLLs by default (--skip-framework to filter)

Usage:
  python decompile_only.py <file.dll>
  python decompile_only.py <folder_with_dlls>
  python decompile_only.py <target> --offline
  python decompile_only.py <target> --skip-framework
"""

import hashlib, os, shutil, subprocess, sys, tarfile, threading, time
import urllib.error, urllib.request, zipfile
import platform
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple

# ─── Global paths ─────────────────────────────────────────────
SCRIPT_DIR   = Path(__file__).resolve().parent
TOOL_DIR     = SCRIPT_DIR / "tools"
DOTNET_DIR   = TOOL_DIR / "dotnet"
ILSPY_DIR    = TOOL_DIR / "ilspy"
DECOMP_CACHE = SCRIPT_DIR / ".dllsec_cache" / "decompiled"
DECOMP_BASE  = SCRIPT_DIR / "Decompiled"

# ── ILSpy / .NET pins ──
ILSPY_VERSION          = "11.1.0.9782"
DOTNET_CHANNEL         = "10.0"
DOTNET_RUNTIME_VERSION = "10.0.12"

DOTNET_EXE: Path = None
ILSPY_EXE:  Path = None

PLATFORM   = platform.system()
IS_WINDOWS = PLATFORM == "Windows"
IS_LINUX   = PLATFORM == "Linux"
IS_MAC     = PLATFORM == "Darwin"

def _exe_name(base: str) -> str:
    return base + (".exe" if IS_WINDOWS else "")

DOTNET_EXE = DOTNET_DIR / _exe_name("dotnet")
ILSPY_EXE  = ILSPY_DIR  / _exe_name("ilspycmd")

# ─── Framework filter (opt-in) ───────────────────────────────
_FRAMEWORK_DIRS = {
    "Microsoft.AspNetCore.App", "Microsoft.AspNetCore.App.Ref",
    "Microsoft.NETCore.App",    "Microsoft.NETCore.App.Ref",
    "Microsoft.WindowsDesktop.App", "Microsoft.WindowsDesktop.App.Ref",
}

def _is_framework_dll(path: Path) -> bool:
    return any(part in _FRAMEWORK_DIRS for part in path.parts)

# ─── Windows long-path helper ────────────────────────────────
def _safe_path(path: Path) -> Path:
    if not IS_WINDOWS:
        return path
    s = str(path) if path.is_absolute() else str(path.absolute())
    if len(s) <= 250 or s.startswith("\\\\?\\"):
        return Path(s)
    if s.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + s[2:])
    return Path("\\\\?\\" + s)

# ─── Unix executable helper ──────────────────────────────────
def _make_executable(path: Path) -> None:
    if IS_WINDOWS:
        return
    try:
        mode = path.stat().st_mode
        path.chmod(mode | 0o111)
    except Exception:
        pass

def _make_tree_executable(root: Path) -> None:
    if IS_WINDOWS:
        return
    for f in root.rglob("*"):
        if f.is_file():
            _make_executable(f)

# ─── Logging ─────────────────────────────────────────────────
_log_lock = threading.Lock()
_log_path: Optional[Path] = None

def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")

def log(msg: str, level: str = "INFO") -> None:
    line = f"[{_ts()}] [{level:<8}] {msg}"
    with _log_lock:
        print(line, flush=True)
        if _log_path:
            with open(_log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")

# ─── Progress bar ────────────────────────────────────────────
class ProgressBar:
    def __init__(self, total: int, label: str = "", width: int = 40):
        self.total = max(total, 1)
        self.label = label
        self.width = width
        self._done = 0
        self._lock = threading.Lock()

    def advance(self, n: int = 1) -> None:
        with self._lock:
            self._done = min(self._done + n, self.total)
            self._render()

    def _render(self) -> None:
        pct = self._done / self.total
        filled = int(self.width * pct)
        bar = "█" * filled + "░" * (self.width - filled)
        line = f"\r  {self.label:<22} [{bar}] {self._done}/{self.total} ({pct*100:.0f}%)"
        sys.stdout.write(line)
        sys.stdout.flush()
        if self._done >= self.total:
            sys.stdout.write("\n")
            sys.stdout.flush()

# ─── Network helpers ────────────────────────────────────────
OFFLINE_MODE = False

def _download_file(url: str, dest: Path, tries: int = 3) -> bool:
    if OFFLINE_MODE:
        log(f"--offline set, refusing to download {url}", "ERROR")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, tries + 1):
        try:
            log(f"Downloading {url} (attempt {attempt}/{tries}) …")
            req = urllib.request.Request(url, headers={"User-Agent": "dll-decompiler"})
            with urllib.request.urlopen(req, timeout=30) as resp, open(dest, "wb") as out:
                shutil.copyfileobj(resp, out)
            return True
        except Exception as e:
            if attempt < tries:
                time.sleep(3 * (2 ** (attempt - 1)))
            else:
                log(f"Download failed: {e}", "ERROR")
    return False

def _run(cmd: List[str], env: Optional[dict] = None, timeout: int = 1200,
         stream: bool = True) -> int:
    log(f"Running: {' '.join(cmd)}")
    try:
        if stream:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, env=env, bufsize=1)
            try:
                for line in proc.stdout:
                    line = line.rstrip()
                    if line:
                        print(f"    | {line}", flush=True)
                proc.wait(timeout=timeout)
                return proc.returncode
            except subprocess.TimeoutExpired:
                proc.kill()
                log(f"Command timed out after {timeout}s", "ERROR")
                return -1
        else:
            r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)
            return r.returncode
    except FileNotFoundError as e:
        log(f"Executable not found: {e}", "ERROR")
        return -1
    except Exception as e:
        log(f"Subprocess error: {e}", "ERROR")
        return -1

# ─── Version stamps ─────────────────────────────────────────
def _stamp_path(tool_dir: Path) -> Path:
    return tool_dir / ".installed_version"

def _read_stamp(tool_dir: Path) -> Optional[str]:
    p = _stamp_path(tool_dir)
    return p.read_text(encoding="utf-8").strip() if p.exists() else None

def _write_stamp(tool_dir: Path, version: str) -> None:
    _stamp_path(tool_dir).write_text(version, encoding="utf-8")

# ─── PowerShell detection ───────────────────────────────────
def _find_powershell() -> Optional[str]:
    """Find a usable PowerShell on any platform."""
    if IS_WINDOWS:
        candidates = ["powershell.exe", "pwsh.exe", "pwsh"]
    else:
        candidates = ["pwsh", "powershell"]
    for c in candidates:
        p = shutil.which(c)
        if p:
            return p
    return None

# ─── curl/wget detection (for bash installer) ───────────────
def _has_curl() -> bool:
    return shutil.which("curl") is not None

def _has_wget() -> bool:
    return shutil.which("wget") is not None

# ─── .NET SDK + Runtime ─────────────────────────────────────
def _has_dotnet_sdk(dotnet_path: Path) -> bool:
    try:
        r = subprocess.run([str(dotnet_path), "--list-sdks"],
                           capture_output=True, text=True, timeout=30)
        return bool(r.stdout.strip())
    except Exception:
        return False

def _has_dotnet_runtime(dotnet_path: Path, version: str = DOTNET_RUNTIME_VERSION) -> bool:
    try:
        r = subprocess.run([str(dotnet_path), "--list-runtimes"],
                           capture_output=True, text=True, timeout=30)
        return any(version in line for line in r.stdout.splitlines())
    except Exception:
        return False

def _get_system_dotnet() -> Optional[Path]:
    p = shutil.which("dotnet")
    if p:
        candidate = Path(p)
        if _has_dotnet_sdk(candidate):
            return candidate
        log(f"System dotnet at {candidate} is runtime-only (no SDK).", "WARN")
    return None

def _install_dotnet_sdk() -> bool:
    """Install .NET SDK via official script, cross-platform."""
    log(f"Installing .NET SDK {DOTNET_CHANNEL} …")
    DOTNET_DIR.mkdir(parents=True, exist_ok=True)

    if IS_WINDOWS:
        ps = _find_powershell()
        if not ps:
            log("PowerShell not found. Install PowerShell 7+ from "
                "https://aka.ms/powershell-release?tag=stable", "ERROR")
            return False
        script_url  = "https://dot.net/v1/dotnet-install.ps1"
        script_path = TOOL_DIR / "dotnet-install.ps1"
        if not _download_file(script_url, script_path):
            return False
        cmd = [ps, "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", str(script_path), "-Channel", DOTNET_CHANNEL,
               "-InstallDir", str(DOTNET_DIR)]
    else:
        bash = shutil.which("bash") or "/bin/bash"
        if not (os.path.exists(bash)):
            log("bash not found – required for dotnet-install.sh", "ERROR")
            return False
        if not (_has_curl() or _has_wget()):
            log("Neither curl nor wget found – installing one is required "
                "(e.g. apt install curl / apk add curl)", "ERROR")
            return False
        script_url  = "https://dot.net/v1/dotnet-install.sh"
        script_path = TOOL_DIR / "dotnet-install.sh"
        if not _download_file(script_url, script_path):
            return False
        _make_executable(script_path)
        cmd = [bash, str(script_path), "--channel", DOTNET_CHANNEL,
               "--install-dir", str(DOTNET_DIR)]

    rc = _run(cmd, timeout=1200)
    if rc != 0:
        log(f".NET SDK installer exit {rc}", "ERROR")
        return False
    if DOTNET_EXE.exists() and _has_dotnet_sdk(DOTNET_EXE):
        if not IS_WINDOWS:
            _make_tree_executable(DOTNET_DIR)
        log(f".NET SDK {DOTNET_CHANNEL} installed.")
        return True
    return False

def _install_dotnet_runtime(version: str = DOTNET_RUNTIME_VERSION) -> bool:
    log(f"Installing .NET runtime {version} …")

    if IS_WINDOWS:
        ps = _find_powershell()
        if not ps:
            log("PowerShell not found for runtime install.", "ERROR")
            return False
        script_path = TOOL_DIR / "dotnet-install.ps1"
        if not script_path.exists():
            if not _download_file("https://dot.net/v1/dotnet-install.ps1", script_path):
                return False
        cmd = [ps, "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", str(script_path), "-Runtime", "dotnet",
               "-Version", version, "-InstallDir", str(DOTNET_DIR)]
    else:
        bash = shutil.which("bash") or "/bin/bash"
        if not _has_curl() and not _has_wget():
            log("Neither curl nor wget – cannot install runtime via script.", "ERROR")
            return _install_dotnet_runtime_direct(version)
        script_path = TOOL_DIR / "dotnet-install.sh"
        if not script_path.exists():
            if not _download_file("https://dot.net/v1/dotnet-install.sh", script_path):
                return _install_dotnet_runtime_direct(version)
        _make_executable(script_path)
        cmd = [bash, str(script_path), "--runtime", "dotnet",
               "--version", version, "--install-dir", str(DOTNET_DIR)]

    rc = _run(cmd, timeout=600)
    if rc != 0:
        log(f"Runtime installer exit {rc} – trying direct download fallback.", "WARN")
        return _install_dotnet_runtime_direct(version)
    if _has_dotnet_runtime(DOTNET_EXE, version):
        if not IS_WINDOWS:
            _make_tree_executable(DOTNET_DIR)
        log(f".NET runtime {version} installed.")
        return True
    return _install_dotnet_runtime_direct(version)

def _install_dotnet_runtime_direct(version: str) -> bool:
    """Fallback: download runtime archive directly (no bash/curl needed)."""
    log(f"Direct-download fallback for .NET runtime {version} …")

    arch = "x64"
    if platform.machine().lower() in ("aarch64", "arm64"):
        arch = "arm64"
    elif platform.machine().lower() in ("i386", "i686", "x86"):
        arch = "x86"

    if IS_WINDOWS:
        url = f"https://aka.ms/dotnet/{DOTNET_CHANNEL}/dotnet-runtime-win-{arch}.zip"
        dest = DOTNET_DIR / "dotnet-runtime.zip"
    elif IS_MAC:
        url = f"https://aka.ms/dotnet/{DOTNET_CHANNEL}/dotnet-runtime-osx-{arch}.tar.gz"
        dest = DOTNET_DIR / "dotnet-runtime.tar.gz"
    else:
        url = f"https://aka.ms/dotnet/{DOTNET_CHANNEL}/dotnet-runtime-linux-{arch}.tar.gz"
        dest = DOTNET_DIR / "dotnet-runtime.tar.gz"

    if not _download_file(url, dest):
        return False

    try:
        DOTNET_DIR.mkdir(parents=True, exist_ok=True)
        if IS_WINDOWS:
            with zipfile.ZipFile(dest) as zf:
                zf.extractall(DOTNET_DIR)
        else:
            with tarfile.open(dest, "r:gz") as tf:
                tf.extractall(DOTNET_DIR)
            _make_tree_executable(DOTNET_DIR)
        dest.unlink(missing_ok=True)
        return DOTNET_EXE.exists() and _has_dotnet_runtime(DOTNET_EXE, version)
    except Exception as e:
        log(f"Extraction failed: {e}", "ERROR")
        return False

def _ensure_dotnet() -> bool:
    global DOTNET_EXE
    sys_dotnet = _get_system_dotnet()
    if sys_dotnet:
        DOTNET_EXE = sys_dotnet
        log(f"Using system .NET SDK: {sys_dotnet}")
        if not _has_dotnet_runtime(DOTNET_EXE, DOTNET_RUNTIME_VERSION):
            log(f"Installing runtime {DOTNET_RUNTIME_VERSION} into system SDK …", "WARN")
            _install_dotnet_runtime(DOTNET_RUNTIME_VERSION)
        return True

    if DOTNET_EXE.exists() and _has_dotnet_sdk(DOTNET_EXE):
        if not _has_dotnet_runtime(DOTNET_EXE, DOTNET_RUNTIME_VERSION):
            _install_dotnet_runtime(DOTNET_RUNTIME_VERSION)
        return True

    if not _install_dotnet_sdk():
        return False
    if not _has_dotnet_runtime(DOTNET_EXE, DOTNET_RUNTIME_VERSION):
        _install_dotnet_runtime(DOTNET_RUNTIME_VERSION)
    return True

# ─── ILSpy ───────────────────────────────────────────────────
def verify_ilspy() -> bool:
    if not ILSPY_EXE.exists():
        log(f"ILSpy executable not found at {ILSPY_EXE}", "ERROR")
        return False
    try:
        env = os.environ.copy()
        env["DOTNET_ROOT"] = str(DOTNET_DIR)
        env["PATH"] = str(DOTNET_DIR) + os.pathsep + env.get("PATH", "")
        r = subprocess.run([str(ILSPY_EXE), "--version"],
                           capture_output=True, text=True, timeout=30, env=env)
        if r.returncode == 0 and r.stdout.strip():
            log(f"ILSpy version: {r.stdout.strip()}")
            return True
        log(f"ILSpy verification failed (exit {r.returncode}).", "ERROR")
        if r.stderr: log(f"stderr: {r.stderr[:200]}", "ERROR")
        if r.stdout: log(f"stdout: {r.stdout[:200]}", "ERROR")
        return False
    except Exception as e:
        log(f"ILSpy verify exception: {e}", "ERROR")
        return False

_ilspy_lock     = threading.Lock()
_ilspy_verified = False

def ensure_ilspy() -> bool:
    global _ilspy_verified
    if _ilspy_verified:
        return True
    with _ilspy_lock:
        if _ilspy_verified:
            return True

        if _read_stamp(ILSPY_DIR) == ILSPY_VERSION and verify_ilspy():
            log(f"ILSpy {ILSPY_VERSION} already installed.")
            _ilspy_verified = True
            return True

        if OFFLINE_MODE:
            log("ILSpy missing and --offline set. Install it or run without --offline.", "ERROR")
            return False

        if not _ensure_dotnet():
            log(".NET SDK + runtime required for ILSpy installation", "ERROR")
            return False

        ILSPY_DIR.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["DOTNET_ROOT"] = str(DOTNET_DIR)
        env["PATH"] = str(DOTNET_DIR) + os.pathsep + env.get("PATH", "")
        cmd = [str(DOTNET_EXE), "tool", "install", "ilspycmd",
               "--version", ILSPY_VERSION, "--tool-path", str(ILSPY_DIR)]

        log("Installing ILSpy from NuGet …")
        rc = _run(cmd, env=env, timeout=300)
        if rc != 0:
            log("ILSpy installation failed", "ERROR")
            return False

        # On Linux/macOS, the shim needs +x
        if not IS_WINDOWS:
            _make_tree_executable(ILSPY_DIR)

        if not ILSPY_EXE.exists():
            log(f"After install, ILSpy not found at {ILSPY_EXE}", "ERROR")
            return False

        if verify_ilspy():
            _write_stamp(ILSPY_DIR, ILSPY_VERSION)
            _ilspy_verified = True
            return True

        log("ILSpy verification failed after installation.", "ERROR")
        return False

# ─── Decompile ──────────────────────────────────────────────
def get_cached_decompiled(sha256: str) -> Optional[Path]:
    d = DECOMP_CACHE / sha256
    if d.exists() and any(d.rglob("*.cs")):
        return d
    return None

def decompile(dll_path: Path, out_dir: Path, sha256: str) -> bool:
    cache_dir = DECOMP_CACHE / sha256
    cached = get_cached_decompiled(sha256)

    if cached:
        log(f"Using cached decompiled sources for {dll_path.name}")
        if out_dir.exists():
            shutil.rmtree(out_dir, ignore_errors=True)
        shutil.copytree(cached, out_dir, symlinks=False, ignore_dangling_symlinks=True)
        return True

    if not ensure_ilspy():
        return False

    if cache_dir.exists():
        shutil.rmtree(cache_dir, ignore_errors=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["DOTNET_ROOT"] = str(DOTNET_DIR)
    env["PATH"] = str(DOTNET_DIR) + os.pathsep + env.get("PATH", "")
    env["DOTNET_ROLL_FORWARD"] = "Major"

    cmd = [str(ILSPY_EXE), str(dll_path), "-p", "-o", str(cache_dir)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, env=env)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "")[:300]
            log(f"Decompile error: {err}", "ERROR")
            return False
    except Exception as e:
        log(f"Decompile exception: {e}", "ERROR")
        return False

    cs = list(cache_dir.rglob("*.cs"))
    if not cs:
        log(f"No .cs files generated for {dll_path.name}", "ERROR")
        return False

    log(f"Decompiled {dll_path.name} → {len(cs)} .cs files (cached)")
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    shutil.copytree(cache_dir, out_dir, symlinks=False, ignore_dangling_symlinks=True)
    return True

# ─── Process one DLL ────────────────────────────────────────
def process_dll(dll_path: Path, session_out: Path, pb: ProgressBar,
                skip_framework: bool = False) -> Tuple[str, bool]:
    name = dll_path.stem

    if skip_framework and _is_framework_dll(dll_path):
        log(f"[{name}] Skipping .NET framework DLL")
        pb.advance()
        return (name, False)

    log(f"[{name}] Reading metadata …")
    try:
        safe = _safe_path(dll_path)
        if not safe.exists():
            log(f"[{name}] Cannot access (long path or missing): {dll_path}", "ERROR")
            pb.advance()
            return (name, False)
        raw    = safe.read_bytes()
        sha256 = hashlib.sha256(raw).hexdigest()
    except Exception as e:
        log(f"[{name}] Metadata error: {e}", "ERROR")
        pb.advance()
        return (name, False)

    out_dir = session_out / f"{name}_{sha256[:8]}"
    log(f"[{name}] Decompiling (SHA: {sha256[:8]}) …")
    ok = decompile(safe, out_dir, sha256)
    log(f"[{name}] {'✅ OK' if ok else '❌ Failed'} → {out_dir}")
    pb.advance()
    return (name, ok)

# ─── Main ────────────────────────────────────────────────────
def main() -> None:
    global OFFLINE_MODE, _log_path

    flags        = {"--offline", "--skip-framework"}
    args         = [a for a in sys.argv[1:] if a not in flags]
    OFFLINE_MODE = "--offline" in sys.argv[1:]
    skip_fw      = "--skip-framework" in sys.argv[1:]

    if len(args) != 1:
        print(__doc__)
        sys.exit(1)

    target = Path(args[0])
    if not target.exists():
        print(f"[ERROR] Path not found: {target}")
        sys.exit(1)

    if target.is_file() and target.suffix.lower() == ".dll":
        dll_files = [target]
    elif target.is_dir():
        dll_files = sorted(target.rglob("*.dll"))
    else:
        print("[ERROR] Provide a .dll file or a directory.")
        sys.exit(1)

    if not dll_files:
        print(f"[ERROR] No .dll files found under: {target}")
        sys.exit(1)

    if skip_fw:
        before = len(dll_files)
        dll_files = [d for d in dll_files if not _is_framework_dll(d)]
        skipped = before - len(dll_files)
        if skipped:
            print(f"  Skipped {skipped} .NET framework DLLs (--skip-framework)")
        if not dll_files:
            print("[ERROR] No DLLs remain after framework filter.")
            sys.exit(1)

    DECOMP_CACHE.mkdir(parents=True, exist_ok=True)
    DECOMP_BASE.mkdir(parents=True, exist_ok=True)

    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = DECOMP_BASE / run_ts
    session_dir.mkdir(parents=True, exist_ok=True)
    _log_path = session_dir / "decompile.log"

    log("=" * 60)
    log("  DLL Decompiler – Cross-Platform")
    log("=" * 60)
    log(f"Platform      : {PLATFORM} ({platform.machine()})")
    log(f"Target        : {target}")
    log(f"DLLs found    : {len(dll_files)}")
    log(f"Skip framework: {skip_fw}")
    log(f"Session dir   : {session_dir}")
    log(f"Offline mode  : {OFFLINE_MODE}")

    if not ensure_ilspy():
        log("CRITICAL: ILSpy could not be installed. Aborting.", "ERROR")
        sys.exit(1)

    pb = ProgressBar(len(dll_files), label="Decompiling")
    results = []

    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(process_dll, dll, session_dir, pb, skip_fw): dll
                for dll in dll_files}
        for fut in as_completed(futs):
            try:
                results.append(fut.result())
            except Exception as e:
                log(f"Worker error: {e}", "ERROR")

    ok_cnt = sum(1 for _, ok in results if ok)
    print("\n" + "═" * 60)
    print(f"  Decompiled {ok_cnt} / {len(dll_files)} DLLs successfully.")
    print(f"  Session folder: {session_dir}")
    print("═" * 60)
    for name, ok in results:
        print(f"  {'✅' if ok else '❌'} {name}")
    print("═" * 60)

if __name__ == "__main__":
    main()