"""
Robust execution and streaming helpers for FFmpeg, FFprobe, and external processes.
Features:
- Concurrent draining of stdout and stderr to eliminate backpressure deadlocks.
- Dual timeout mechanism:
    * Stall timeout: detects inactivity (no progress on stdout or stderr)
    * Total adaptive timeout: max(base_timeout, duration * multiplier)
- Clean, recursive process-tree termination on Windows and POSIX to avoid orphan zombies.
- Drops process priority to BELOW_NORMAL on Windows to protect GUI responsiveness.
"""

import os
import sys
import time
import signal
import subprocess
import threading
from typing import Optional, Tuple, List, Union


class ProcessStallTimeoutError(RuntimeError):
    """Raised when an external process ceases to produce output for longer than stall_timeout."""
    pass


def _get_low_priority_startupinfo() -> Optional[subprocess.STARTUPINFO]:
    """Returns STARTUPINFO hiding console window on Windows."""
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return si
    return None


def set_process_low_priority(proc: subprocess.Popen) -> None:
    """Drops process to BELOW_NORMAL CPU priority on Windows."""
    if sys.platform != "win32" or proc.pid is None:
        return
    try:
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(
            0x0200,  # PROCESS_SET_INFORMATION
            False,
            proc.pid
        )
        if handle:
            _BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
            ctypes.windll.kernel32.SetPriorityClass(handle, _BELOW_NORMAL_PRIORITY_CLASS)
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        pass


def terminate_process_tree(proc: subprocess.Popen, timeout: float = 3.0) -> None:
    """
    Terminates an external subprocess and all its recursive children.
    Specifically reserved for external child processes (ffmpeg, ffprobe, fpcalc),
    NOT for internal ProcessPoolExecutor workers.
    """
    if proc is None or proc.poll() is not None:
        _close_proc_pipes(proc)
        return

    pid = proc.pid

    # 1. Try using psutil if available
    try:
        import psutil
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
            for child in children:
                try:
                    child.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            try:
                parent.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            psutil.wait_procs(children + [parent], timeout=timeout)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    except ImportError:
        # 2. Platform fallback without psutil
        if sys.platform == "win32":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=timeout,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
                )
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        else:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    _close_proc_pipes(proc)
    try:
        proc.wait(timeout=1.0)
    except Exception:
        pass


def _close_proc_pipes(proc: subprocess.Popen) -> None:
    """Safely closes open pipes of a subprocess."""
    if proc is None:
        return
    for pipe in (proc.stdin, proc.stdout, proc.stderr):
        if pipe is not None and not getattr(pipe, "closed", True):
            try:
                pipe.close()
            except Exception:
                pass


def run_command_with_drain(
    cmd: List[str],
    base_timeout: float = 30.0,
    duration_seconds: float = 0.0,
    stall_timeout: float = 10.0,
    multiplier: float = 1.5,
    collect_stdout: bool = True,
    collect_stderr: bool = True,
) -> Tuple[int, bytes, bytes]:
    """
    Executes an external command with concurrent draining of both stdout and stderr.
    Uses read1() for non-blocking stream reads so small progress chunks reset the stall timer.
    """
    if duration_seconds > 0.0:
        total_timeout = max(base_timeout, (duration_seconds * multiplier) + 5.0)
    else:
        total_timeout = base_timeout

    startupinfo = _get_low_priority_startupinfo()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        startupinfo=startupinfo
    )
    set_process_low_priority(proc)

    stdout_chunks: List[bytes] = []
    stderr_chunks: List[bytes] = []
    last_activity_lock = threading.Lock()
    last_activity = time.monotonic()
    stopped_event = threading.Event()

    def _read_stdout():
        nonlocal last_activity
        read_fn = getattr(proc.stdout, "read1", proc.stdout.read)
        try:
            while not stopped_event.is_set():
                chunk = read_fn(65536)
                if not chunk:
                    break
                with last_activity_lock:
                    last_activity = time.monotonic()
                if collect_stdout:
                    stdout_chunks.append(chunk)
        except Exception:
            pass

    def _read_stderr():
        nonlocal last_activity
        read_fn = getattr(proc.stderr, "read1", proc.stderr.read)
        try:
            while not stopped_event.is_set():
                chunk = read_fn(4096)
                if not chunk:
                    break
                with last_activity_lock:
                    last_activity = time.monotonic()
                if collect_stderr:
                    stderr_chunks.append(chunk)
        except Exception:
            pass

    t_out = threading.Thread(target=_read_stdout, daemon=True)
    t_err = threading.Thread(target=_read_stderr, daemon=True)
    t_out.start()
    t_err.start()

    start_time = time.monotonic()
    timed_out = False
    stalled = False

    try:
        while proc.poll() is None:
            now = time.monotonic()
            if now - start_time > total_timeout:
                timed_out = True
                break

            with last_activity_lock:
                inactive_duration = now - last_activity
            if inactive_duration > stall_timeout:
                stalled = True
                break

            time.sleep(0.05)

        if timed_out or stalled:
            terminate_process_tree(proc)
            stopped_event.set()
            t_out.join(timeout=1.0)
            t_err.join(timeout=1.0)
            if timed_out:
                raise subprocess.TimeoutExpired(cmd, total_timeout)
            if stalled:
                raise ProcessStallTimeoutError(
                    f"Process stalled: no output on stdout/stderr for {stall_timeout:.1f}s (cmd: {cmd[0]})"
                )

        proc.wait(timeout=2.0)
        stopped_event.set()
        t_out.join(timeout=2.0)
        t_err.join(timeout=2.0)

        out = b"".join(stdout_chunks) if collect_stdout else b""
        err = b"".join(stderr_chunks) if collect_stderr else b""
        return proc.returncode, out, err

    finally:
        stopped_event.set()
        terminate_process_tree(proc)
