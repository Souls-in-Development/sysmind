"""Windows system probes.

UNVERIFIED. Every function here was written from documentation and has never
run on Windows. It implements the same names and return shapes as
sysmind_scan_linux, which is the tested reference.

Where Linux parses text tables, this asks PowerShell for JSON and parses that.
Text output on Windows varies with locale and console width; JSON does not. It
is also the difference between a probe that fails cleanly and one that silently
returns nonsense in a language nobody tested against.

Every probe returns a safe empty value on failure rather than raising, matching
the Linux module's behaviour: a missing probe degrades the system brief, it
does not stop the program.
"""
import json
import subprocess
from typing import Any, Dict, List, Optional

NAME = "windows"

_PS = ["powershell", "-NoProfile", "-NonInteractive", "-Command"]


def _ps(script: str, timeout: float = 20.0) -> Optional[Any]:
    """Run PowerShell and parse its JSON output. None on any failure."""
    try:
        r = subprocess.run(_PS + [script], capture_output=True, text=True,
                           timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        return json.loads(r.stdout)
    except (ValueError, TypeError):
        return None


def _as_list(value: Any) -> List[Any]:
    """PowerShell emits a bare object for one result and an array for many."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def get_system_info() -> Dict[str, Any]:
    info = {"kernel": "unknown", "os": "unknown", "uptime": "unknown"}
    data = _ps(
        "$o = Get-CimInstance Win32_OperatingSystem; "
        "[pscustomobject]@{ "
        "  os = $o.Caption; build = $o.Version; "
        "  boot = $o.LastBootUpTime.ToString('o') "
        "} | ConvertTo-Json")
    if isinstance(data, dict):
        info["os"] = data.get("os") or "unknown"
        info["kernel"] = "Windows build {}".format(data.get("build", "?"))
        if data.get("boot"):
            info["uptime"] = "since {}".format(str(data["boot"])[:16])
    return info


def get_packages() -> Dict[str, Any]:
    result = {"installed": 0, "upgradable": 0, "orphaned": 0}
    installed = _ps(
        "$p = winget list --disable-interactivity 2>$null | "
        "Measure-Object -Line; "
        "[pscustomobject]@{ n = $p.Lines } | ConvertTo-Json")
    if isinstance(installed, dict) and isinstance(installed.get("n"), int):
        # winget prints a header block; subtract it rather than report it.
        result["installed"] = max(installed["n"] - 4, 0)

    upgradable = _ps(
        "$u = winget upgrade --disable-interactivity 2>$null | "
        "Measure-Object -Line; "
        "[pscustomobject]@{ n = $u.Lines } | ConvertTo-Json")
    if isinstance(upgradable, dict) and isinstance(upgradable.get("n"), int):
        result["upgradable"] = max(upgradable["n"] - 4, 0)
    return result


def get_services() -> Dict[str, Any]:
    result = {"running": 0, "failed": 0, "enabled": 0,
              "listening_ports": [], "failed_services": []}

    running = _ps("(Get-Service | Where-Object Status -eq 'Running')."
                  "Count | ConvertTo-Json")
    if isinstance(running, int):
        result["running"] = running

    # The closest analogue to a failed unit: set to start automatically, but
    # not running.
    failed = _ps(
        "Get-Service | Where-Object { $_.StartType -eq 'Automatic' -and "
        "$_.Status -ne 'Running' } | Select-Object -First 10 -"
        "ExpandProperty Name | ConvertTo-Json")
    names = [str(n) for n in _as_list(failed)]
    result["failed"] = len(names)
    result["failed_services"] = names[:10]

    ports = _ps(
        "Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | "
        "Select-Object -ExpandProperty LocalPort | Sort-Object -Unique | "
        "ConvertTo-Json")
    result["listening_ports"] = sorted({int(p) for p in _as_list(ports)
                                        if isinstance(p, int)})
    return result


def get_resources() -> Dict[str, Any]:
    result: Dict[str, Any] = {"disk_root_percent": 0, "memory_percent": 0,
                              "load": 0.0}
    disk = _ps(
        "$d = Get-PSDrive -Name ($env:SystemDrive[0]) ; "
        "[pscustomobject]@{ used = $d.Used; free = $d.Free } | ConvertTo-Json")
    if isinstance(disk, dict):
        used, free = disk.get("used") or 0, disk.get("free") or 0
        total = used + free
        if total:
            result["disk_root_percent"] = int(used / total * 100)
            result["disk_root_size"] = "{} GB".format(round(total / 1e9))
            result["disk_root_avail"] = "{} GB".format(round(free / 1e9))

    mem = _ps(
        "$o = Get-CimInstance Win32_OperatingSystem; "
        "[pscustomobject]@{ total = $o.TotalVisibleMemorySize; "
        "free = $o.FreePhysicalMemory } | ConvertTo-Json")
    if isinstance(mem, dict):
        total, free = mem.get("total") or 0, mem.get("free") or 0
        if total:
            used_kb = total - free
            result["memory_percent"] = int(used_kb / total * 100)
            result["memory_mb"] = int(used_kb / 1024)

    # Windows has no load average. Processor queue length is the nearest
    # equivalent, and is reported as such rather than pretending otherwise.
    load = _ps(
        "$c = (Get-CimInstance Win32_PerfFormattedData_PerfOS_System)."
        "ProcessorQueueLength; [pscustomobject]@{ q = $c } | ConvertTo-Json")
    if isinstance(load, dict) and load.get("q") is not None:
        try:
            result["load"] = float(load["q"])
        except (TypeError, ValueError):
            pass
    return result


def get_security(paranoia: str = "medium") -> Dict[str, Any]:
    result: Dict[str, Any] = {"open_ports": [], "recent_logins": [],
                              "suid_bins": []}
    ports = _ps(
        "Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | "
        "Select-Object -ExpandProperty LocalPort | Sort-Object -Unique | "
        "ConvertTo-Json")
    result["open_ports"] = sorted({int(p) for p in _as_list(ports)
                                   if isinstance(p, int)})

    logins = _ps(
        "Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4624} "
        "-MaxEvents 5 -ErrorAction SilentlyContinue | "
        "Select-Object -ExpandProperty TimeCreated | ConvertTo-Json")
    result["recent_logins"] = [str(x) for x in _as_list(logins)][:5]

    if paranoia in ("medium", "high"):
        # No SUID on Windows. The comparable question is what runs with full
        # privilege, so this reports auto-start services running as SYSTEM.
        elevated = _ps(
            "Get-CimInstance Win32_Service | Where-Object { "
            "$_.StartName -eq 'LocalSystem' -and $_.State -eq 'Running' } | "
            "Select-Object -First 20 -ExpandProperty Name | ConvertTo-Json")
        result["suid_bins"] = [str(x) for x in _as_list(elevated)][:20]
    return result


def get_configs() -> List[str]:
    """Recently changed machine configuration, the analogue of /etc churn."""
    data = _ps(
        "Get-ChildItem -Path $env:SystemRoot\\System32\\config -File "
        "-ErrorAction SilentlyContinue | "
        "Where-Object { $_.LastWriteTime -gt (Get-Date).AddDays(-7) } | "
        "Select-Object -First 50 -ExpandProperty FullName | ConvertTo-Json")
    return [str(x) for x in _as_list(data)][:50]


def get_logs() -> List[str]:
    data = _ps(
        "Get-WinEvent -FilterHashtable @{LogName='System'; Level=1,2; "
        "StartTime=(Get-Date).AddDays(-1)} -MaxEvents 30 "
        "-ErrorAction SilentlyContinue | "
        "ForEach-Object { \"$($_.TimeCreated) $($_.ProviderName): "
        "$($_.Message)\" } | ConvertTo-Json")
    return [" ".join(str(x).split())[:300] for x in _as_list(data)][:30]


def get_platform_extras() -> Dict[str, Any]:
    """Windows equivalents of the Parrot tooling checks."""
    data = _ps(
        "[pscustomobject]@{ "
        "  defender = [bool](Get-Service WinDefend -ErrorAction "
        "SilentlyContinue | Where-Object Status -eq 'Running'); "
        "  firewall = [bool](Get-NetFirewallProfile -ErrorAction "
        "SilentlyContinue | Where-Object Enabled -eq $true); "
        "  bitlocker = [bool](Get-Command Get-BitLockerVolume "
        "-ErrorAction SilentlyContinue) "
        "} | ConvertTo-Json")
    if isinstance(data, dict):
        return {"defender_running": bool(data.get("defender")),
                "firewall_enabled": bool(data.get("firewall")),
                "bitlocker_available": bool(data.get("bitlocker"))}
    return {"defender_running": False, "firewall_enabled": False,
            "bitlocker_available": False}
