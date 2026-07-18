"""Linux system probes. Extracted verbatim from sysmind_scan.

Each function returns the shape sysmind_scan expects; a platform module for
another OS implements the same names and shapes. Nothing here changed when it
moved - this is the reference implementation the interface was derived from.
"""
import json
import subprocess
from typing import Any, Dict, List

from sysmind_common import run_cmd

NAME = "linux"


def get_system_info() -> Dict[str, Any]:
    info = {}
    try:
        r = run_cmd(["uname", "-a"], check=False)
        info["kernel"] = r.stdout.strip()
    except Exception:
        info["kernel"] = "unknown"
    try:
        with open("/etc/os-release") as f:
            lines = f.read().splitlines()
            info["os"] = next((l.split("=", 1)[1].strip('"') for l in lines if l.startswith("PRETTY_NAME")), "unknown")
    except Exception:
        info["os"] = "unknown"
    try:
        r = run_cmd(["uptime", "-p"], check=False)
        info["uptime"] = r.stdout.strip()
    except Exception:
        info["uptime"] = "unknown"
    return info


def get_packages() -> Dict[str, Any]:
    result = {"installed": 0, "upgradable": 0, "orphaned": 0}
    try:
        r = run_cmd(["dpkg-query", "-W"], check=False)
        result["installed"] = len(r.stdout.strip().splitlines()) if r.stdout else 0
    except Exception:
        pass
    try:
        r = run_cmd(["apt", "list", "--upgradable"], check=False)
        lines = [l for l in r.stdout.splitlines() if l.strip() and not l.startswith("Listing")]
        result["upgradable"] = len(lines)
    except Exception:
        pass
    return result


def get_services() -> Dict[str, Any]:
    result = {"running": 0, "failed": 0, "enabled": 0, "listening_ports": []}
    try:
        r = run_cmd(["systemctl", "list-units", "--type=service", "--state=running", "--no-pager"], check=False)
        result["running"] = len([l for l in r.stdout.splitlines() if ".service" in l])
    except Exception:
        pass
    try:
        r = run_cmd(["systemctl", "list-units", "--type=service", "--state=failed", "--no-pager"], check=False)
        failed = [l.split()[0] for l in r.stdout.splitlines() if ".service" in l]
        result["failed"] = len(failed)
        result["failed_services"] = failed[:10]
    except Exception:
        result["failed_services"] = []
    try:
        r = run_cmd(["ss", "-tlnp"], check=False)
        ports = []
        for line in r.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 4:
                local = parts[3]
                if ":" in local:
                    port = local.split(":")[-1]
                    if port.isdigit():
                        ports.append(int(port))
        result["listening_ports"] = sorted(set(ports))
    except Exception:
        pass
    return result


def get_resources() -> Dict[str, Any]:
    result = {}
    try:
        r = run_cmd(["df", "-h", "/"], check=False)
        lines = r.stdout.strip().splitlines()
        if len(lines) >= 2:
            parts = lines[1].split()
            result["disk_root_percent"] = int(parts[4].rstrip("%"))
            result["disk_root_size"] = parts[1]
            result["disk_root_avail"] = parts[3]
    except Exception:
        result["disk_root_percent"] = 0
    try:
        r = run_cmd(["free", "-m"], check=False)
        lines = r.stdout.strip().splitlines()
        if lines:
            mem = lines[1].split()
            total = int(mem[1])
            used = int(mem[2])
            result["memory_percent"] = int(used / total * 100) if total else 0
            result["memory_mb"] = used
    except Exception:
        result["memory_percent"] = 0
    try:
        r = run_cmd(["cat", "/proc/loadavg"], check=False)
        result["load"] = float(r.stdout.strip().split()[0])
    except Exception:
        result["load"] = 0.0
    return result


def get_security(paranoia: str = "medium") -> Dict[str, Any]:
    result = {"open_ports": [], "recent_logins": [], "suid_bins": []}
    try:
        r = run_cmd(["ss", "-tlnp"], check=False)
        ports = []
        for line in r.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 4:
                local = parts[3]
                if ":" in local:
                    port = local.split(":")[-1]
                    if port.isdigit():
                        ports.append(int(port))
        result["open_ports"] = sorted(set(ports))
    except Exception:
        pass
    try:
        r = run_cmd(["last", "-n", "5"], check=False)
        result["recent_logins"] = [l.strip() for l in r.stdout.splitlines()[:5] if l.strip()]
    except Exception:
        pass
    if paranoia in ("medium", "high"):
        try:
            r = run_cmd(["find", "/usr", "-perm", "-4000", "-type", "f"], check=False)
            result["suid_bins"] = r.stdout.strip().splitlines()[:20]
        except Exception:
            pass
    return result


def get_configs() -> List[str]:
    try:
        r = run_cmd(["find", "/etc", "-type", "f", "-mtime", "-7"], check=False)
        return r.stdout.strip().splitlines()[:50]
    except Exception:
        return []


def get_logs() -> List[str]:
    try:
        r = run_cmd(["journalctl", "--priority=3", "--since=yesterday", "--no-pager"], check=False)
        lines = r.stdout.strip().splitlines()
        return lines[-30:] if len(lines) > 30 else lines
    except Exception:
        return []


def get_platform_extras() -> Dict[str, Any]:
    result = {}
    try:
        r = run_cmd(["which", "anonsurf"], check=False)
        result["anonsurf_available"] = r.returncode == 0
    except Exception:
        result["anonsurf_available"] = False
    try:
        r = run_cmd(["which", "firejail"], check=False)
        result["firejail_available"] = r.returncode == 0
    except Exception:
        result["firejail_available"] = False
    return result
