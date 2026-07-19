"""A template must never reach the human as something to approve.

From a live run with qwen3:8b + qwen2.5-coder:1.5b, the coder returned
`du -sh /path/to/directory/*`. It parses as shell and reads as a command, so
neither `bash -n` nor looks_like_command() rejected it. It was explained
confidently in Urdu and given a well-formed permission question - a fluent
request to approve something that does nothing.
"""
import _bootstrap  # noqa: F401

from sysmind_turn import extract_command, has_placeholder

TEMPLATES = [
    "du -sh /path/to/directory/*",
    "systemctl restart <service-name>",
    "apt install YOUR_PACKAGE",
    "curl https://example.com/api",
    "cp {{source}} /backup",
    "ssh your-server.local",
]

REAL = [
    "du -h /var/log | sort -rh | head -10",
    "systemctl list-units --state=failed",
    "apt list --upgradable",
    "find /var/log -name '*.gz' -delete",
    "last | tail -n 20",
    "ss -tuln",
    "df -h /var /tmp /home",
    "journalctl --since yesterday --priority=3",
    "dpkg -l | grep ^ii | wc -l",
    "chmod 640 /etc/shadow",
]


def test_templates_are_rejected():
    for cmd in TEMPLATES:
        assert has_placeholder(cmd), f"template not caught: {cmd}"


def test_real_commands_are_not_rejected():
    for cmd in REAL:
        assert not has_placeholder(cmd), f"false positive: {cmd}"


def test_the_observed_failure_yields_nothing_to_approve():
    assert extract_command("```bash\ndu -sh /path/to/directory/*\n```") is None


def test_a_real_command_still_gets_through():
    fenced = "```bash\ndu -h /var/log | sort -rh | head -10\n```"
    assert extract_command(fenced) == "du -h /var/log | sort -rh | head -10"


if __name__ == "__main__":
    test_templates_are_rejected()
    test_real_commands_are_not_rejected()
    test_the_observed_failure_yields_nothing_to_approve()
    test_a_real_command_still_gets_through()
    print("PASS: placeholders")
