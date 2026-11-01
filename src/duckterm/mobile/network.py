"""Read-only discovery of active physical macOS LAN interfaces.

Hardware-port inventory avoids assuming that en0 is Wi-Fi. Interface flags and
link status then exclude disconnected ports; explicit address ranges avoid the
broader meaning of ipaddress.is_private. Unknown platforms/formats never fall
back to a default route, hostname resolution, or a VPN address.
"""

import ipaddress
import re
import subprocess
import sys
from dataclasses import dataclass

_LAN_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
)
_INTERFACE = re.compile(r"en[0-9]+", re.ASCII)
_HEADER = re.compile(r"^(\S+): flags=[0-9a-fA-F]+<([^>]*)>.*$", re.MULTILINE)
_HOST_LABEL = re.compile(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", re.ASCII)
_UNUSABLE_ADDRESS_FLAGS = frozenset(
    {"tentative", "duplicated", "detached", "deprecated", "temporary"}
)


@dataclass(frozen=True)
class AddressCandidate:
    interface: str
    address: str

    @property
    def family(self) -> int:
        return ipaddress.ip_address(self.address).version


def _read(*command: str) -> str:
    if sys.platform != "darwin":
        raise ValueError("Automatic Ducky network discovery requires macOS; use --address")
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
            env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C", "LANG": "C"},
        )
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise ValueError("Could not read macOS network settings; use --address") from exc
    return result.stdout


def _hardware_interfaces(output: str) -> set[str]:
    """Parse networksetup's Hardware Port / Device records, not service names."""
    interfaces: set[str] = set()
    records = re.split(r"(?m)^Hardware Port: ", output)
    if len(records) == 1:
        raise ValueError("Unrecognized macOS hardware-port inventory; use --address")
    for record in records[1:]:
        label, _, body = record.partition("\n")
        devices = re.findall(r"(?m)^Device: (\S+)\s*$", body)
        if len(devices) != 1:
            raise ValueError("Incomplete macOS hardware-port inventory; use --address")
        device = devices[0]
        words = set(re.findall(r"[a-z0-9]+", label.lower()))
        # networksetup may also list bridges. enN plus a physical port label
        # excludes AWDL, loopback, tunnels, Bluetooth, and Thunderbolt bridges.
        excluded = {"bridge", "bluetooth", "vpn", "virtual", "tunnel"}
        physical = label in {"Wi-Fi", "AirPort"} or bool(words & {"ethernet", "lan"})
        if _INTERFACE.fullmatch(device) and physical and not words & excluded:
            interfaces.add(device)
    return interfaces


def _parse_candidates(hardware: str, interfaces: str) -> list[AddressCandidate]:
    physical = _hardware_interfaces(hardware)
    blocks = list(_HEADER.finditer(interfaces))
    if not blocks or any(
        line and not line[0].isspace() and not _HEADER.fullmatch(line)
        for line in interfaces.splitlines()
    ):
        raise ValueError("Unrecognized macOS interface status; use --address")
    candidates: set[AddressCandidate] = set()
    seen: set[str] = set()
    for index, match in enumerate(blocks):
        name, flags_text = match.groups()
        if name in seen:
            raise ValueError("Duplicate macOS interface status; use --address")
        seen.add(name)
        if name not in physical:
            continue
        flags = set(flags_text.split(","))
        end = blocks[index + 1].start() if index + 1 < len(blocks) else len(interfaces)
        body = interfaces[match.end() : end]
        statuses = re.findall(r"(?m)^\s+status: (\S+)\s*$", body)
        if (
            not {"UP", "RUNNING"} <= flags
            or flags & {"LOOPBACK", "POINTOPOINT"}
            or statuses != ["active"]
        ):
            continue
        for line in body.splitlines():
            parts = line.split()
            if not parts or parts[0] not in {"inet", "inet6"}:
                continue
            if len(parts) < 2:
                raise ValueError("Incomplete macOS interface address; use --address")
            if "%" in parts[1] or _UNUSABLE_ADDRESS_FLAGS.intersection(parts[2:]):
                continue
            try:
                address = ipaddress.ip_address(parts[1])
            except ValueError as exc:
                raise ValueError("Invalid macOS interface address; use --address") from exc
            if (parts[0] == "inet") != (address.version == 4):
                raise ValueError("Invalid macOS interface address family; use --address")
            if any(address in network for network in _LAN_NETWORKS):
                candidates.add(AddressCandidate(name, str(address)))
    return sorted(
        candidates,
        key=lambda item: (item.interface, item.family, int(ipaddress.ip_address(item.address))),
    )


def active_candidates() -> list[AddressCandidate]:
    """Return physical interface/address pairs; an empty list means no usable LAN.

    A private IPv4 and stable ULA on one port are separate candidates. Temporary
    IPv6 addresses are excluded because they should not be published to phones.
    Reads are snapshots, so callers must handle an interface changing afterward.
    """
    hardware = _read("/usr/sbin/networksetup", "-listallhardwareports")
    interfaces = _read("/sbin/ifconfig", "-a")
    return _parse_candidates(hardware, interfaces)


def active_addresses() -> list[str]:
    """Return the distinct addresses from active_candidates, in the same order."""
    return list(dict.fromkeys(item.address for item in active_candidates()))


def detect_address(*, interface: str | None = None, family: int | None = None) -> str:
    """Select exactly one candidate, optionally following a saved port/family."""
    if interface is not None and not _INTERFACE.fullmatch(interface):
        raise ValueError("Ducky automatic discovery needs a physical enN interface; use --address")
    if family is not None and family not in (4, 6):
        raise ValueError("Ducky address family must be 4 or 6")
    candidates = [
        item
        for item in active_candidates()
        if (interface is None or item.interface == interface)
        and (family is None or item.family == family)
    ]
    if not candidates:
        raise ValueError("No active private Wi-Fi or Ethernet address found; use --address")
    if len(candidates) != 1:
        choices = ", ".join(f"{item.interface}={item.address}" for item in candidates)
        raise ValueError(f"Multiple active private network addresses ({choices}); choose --address")
    return candidates[0].address


def local_hostname() -> str:
    """Read the configured Bonjour name without DNS resolution or network writes.

    The name can follow DHCP changes on the same multicast-capable LAN. It is a
    locator, not a replacement for certificate pinning and TLS name validation.
    """
    label = _read("/usr/sbin/scutil", "--get", "LocalHostName").rstrip("\r\n")
    if not _HOST_LABEL.fullmatch(label):
        raise ValueError("This Mac has no valid Bonjour local hostname; use --address")
    return label.lower() + ".local"
