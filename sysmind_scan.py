"""System atlas scanner. Dispatches to the probe module for this platform.

The probes are the one part of platform support that cannot be a table entry:
parsing `df -h` is the output format, not just the command name. So each OS
gets a module implementing the same function names and return shapes, and this
file holds only what is genuinely shared — the alert rules and the assembly.

  sysmind_scan_linux    tested, the reference implementation
  sysmind_scan_windows  written from documentation, never run
"""
import json
from datetime import datetime, timezone
from typing import Any, Dict, List

import sysmind_platform
from sysmind_common import ATLAS_FILE, ensure_dirs

if sysmind_platform.CURRENT.key == "windows":
    import sysmind_scan_windows as probes
else:
    import sysmind_scan_linux as probes

PLATFORM = probes.NAME

# Re-exported so callers and tests can reach the probes without knowing which
# module answered.
get_system_info = probes.get_system_info
get_packages = probes.get_packages
get_services = probes.get_services
get_resources = probes.get_resources
get_security = probes.get_security
get_configs = probes.get_configs
get_logs = probes.get_logs
get_platform_extras = probes.get_platform_extras


def compute_alerts(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Alert rules. Deliberately platform-independent: a full disk is a full
    disk, and these read only the normalised values the probes return."""
    alerts = []
    res = data.get("resources", {})
    if res.get("disk_root_percent", 0) > 80:
        alerts.append({"level": "warn", "category": "disk",
                       "message": f"Root partition {res['disk_root_percent']}% full"})
    if res.get("memory_percent", 0) > 85:
        alerts.append({"level": "warn", "category": "memory",
                       "message": f"Memory usage {res['memory_percent']}%"})
    pkg = data.get("packages", {})
    if pkg.get("upgradable", 0) > 0:
        alerts.append({"level": "info", "category": "packages",
                       "message": f"{pkg['upgradable']} packages can be upgraded"})
    svc = data.get("services", {})
    if svc.get("failed", 0) > 0:
        failed_list = svc.get("failed_services", [])
        alerts.append({"level": "warn", "category": "services",
                       "message": f"{svc['failed']} failed services: "
                                  f"{', '.join(failed_list[:3])}"})
    sec = data.get("security", {})
    if 22 in sec.get("open_ports", []):
        alerts.append({"level": "info", "category": "security",
                       "message": "SSH port (22) is open"})
    if 3389 in sec.get("open_ports", []):
        alerts.append({"level": "info", "category": "security",
                       "message": "Remote Desktop port (3389) is open"})
    return alerts


def scan(paranoia: str = "medium") -> Dict[str, Any]:
    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "platform": PLATFORM,
        "platform_tested": sysmind_platform.CURRENT.tested,
        "system": get_system_info(),
        "packages": get_packages(),
        "services": get_services(),
        "resources": get_resources(),
        "security": get_security(paranoia),
        "configs_modified": get_configs(),
        "recent_logs": get_logs(),
        "platform_extras": get_platform_extras(),
    }
    # Old atlases used "parrot"; kept so an existing file still reads.
    data["parrot"] = data["platform_extras"]
    data["alerts"] = compute_alerts(data)
    return data


def main():
    import sys
    paranoia = sys.argv[1] if len(sys.argv) > 1 else "medium"
    data = scan(paranoia)
    ensure_dirs()
    with open(ATLAS_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Atlas written to {ATLAS_FILE}")
    print(f"Platform: {data['platform']}"
          f"{'' if data['platform_tested'] else '  (UNTESTED on this OS)'}")
    print(f"Alerts: {len(data['alerts'])}")
    for a in data["alerts"]:
        print(f"  [{a['level']}] {a['message']}")


if __name__ == "__main__":
    main()
