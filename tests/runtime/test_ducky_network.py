"""Synthetic macOS command output only: no owner network, DNS, or session state."""

import subprocess

import pytest

from duckterm.mobile import network


def hardware(*ports):
    return "\n\n".join(
        f"Hardware Port: {label}\nDevice: {device}\nEthernet Address: 02:00:00:00:00:01"
        for label, device in ports
    )


def interface(name="en0", addresses=("192.168.12.8",), *, flags=None, status="active"):
    flags = "UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST" if flags is None else flags
    lines = [f"{name}: flags=8863<{flags}> mtu 1500", "\tether 02:00:00:00:00:01"]
    for address in addresses:
        kind = "inet6" if ":" in address else "inet"
        lines.append(f"\t{kind} {address}")
    if status is not None:
        lines.append(f"\tstatus: {status}")
    return "\n".join(lines) + "\n"


@pytest.fixture(autouse=True)
def commands(monkeypatch):
    """No test may accidentally inspect the owner's interfaces or hostname."""
    responses = {
        ("/usr/sbin/networksetup", "-listallhardwareports"): hardware(("Wi-Fi", "en0")),
        ("/sbin/ifconfig", "-a"): interface(),
        ("/usr/sbin/scutil", "--get", "LocalHostName"): "Test-Mac\n",
    }
    calls = []

    def run(command, **kwargs):
        assert command in responses
        assert kwargs == {
            "check": True,
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "timeout": 5,
            "env": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C", "LANG": "C"},
        }
        calls.append(command)
        result = responses[command]
        if isinstance(result, Exception):
            raise result
        return subprocess.CompletedProcess(command, 0, stdout=result, stderr="")

    monkeypatch.setattr(network.sys, "platform", "darwin")
    monkeypatch.setattr(network.subprocess, "run", run)
    return responses, calls


def test_reads_only_fixed_local_commands_with_clean_environment(commands):
    assert network.detect_address() == "192.168.12.8"
    assert commands[1] == [
        ("/usr/sbin/networksetup", "-listallhardwareports"),
        ("/sbin/ifconfig", "-a"),
    ]


@pytest.mark.parametrize(
    "label", ["Wi-Fi", "AirPort", "Ethernet", "USB 10/100/1000 LAN", "Thunderbolt Ethernet"]
)
def test_hardware_inventory_identifies_physical_port_without_assuming_en0(label):
    assert network._parse_candidates(hardware((label, "en7")), interface("en7")) == [
        network.AddressCandidate("en7", "192.168.12.8")
    ]


def test_excludes_tunnels_virtual_ports_and_unlisted_interfaces():
    ports = [
        ("Wi-Fi", "en0"),
        ("Thunderbolt Bridge", "bridge0"),
        ("Ethernet", "utun2"),
        ("Ethernet", "lo0"),
        ("Ethernet", "awdl0"),
        ("Virtual Ethernet", "en8"),
        ("VPN LAN", "en9"),
        ("Bluetooth PAN", "en10"),
    ]
    states = "".join(interface(device) for _, device in ports) + interface("en99")
    assert network._parse_candidates(hardware(*ports), states) == [
        network.AddressCandidate("en0", "192.168.12.8")
    ]


@pytest.mark.parametrize(
    ("flags", "status"),
    [
        ("BROADCAST,RUNNING", "active"),
        ("UP,BROADCAST", "active"),
        ("UP,RUNNING,LOOPBACK", "active"),
        ("UP,RUNNING,POINTOPOINT", "active"),
        ("UP,RUNNING", "inactive"),
        ("UP,RUNNING", None),
    ],
)
def test_excludes_disconnected_or_nonphysical_interface_state(flags, status):
    assert (
        network._parse_candidates(
            hardware(("Ethernet", "en0")), interface(flags=flags, status=status)
        )
        == []
    )


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.1",
        "10.255.255.254",
        "172.16.0.1",
        "172.31.255.254",
        "192.168.0.1",
        "192.168.255.254",
        "fc00::1",
        "fdff:ffff::1",
    ],
)
def test_accepts_only_explicit_rfc1918_and_ula_ranges(address):
    candidates = network._parse_candidates(
        hardware(("Wi-Fi", "en0")), interface(addresses=(address,))
    )
    assert candidates == [network.AddressCandidate("en0", address)]
    assert candidates[0].family == (6 if ":" in address else 4)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "0.0.0.0",
        "169.254.1.2",
        "100.64.0.1",
        "172.15.1.2",
        "172.32.1.2",
        "192.0.0.1",
        "192.0.2.1",
        "198.18.0.1",
        "8.8.8.8",
        "224.0.0.1",
        "255.255.255.255",
        "::",
        "::1",
        "fe80::1%en0",
        "fe80::1",
        "ff02::1",
        "2001:db8::1",
        "2001:4860::1",
        "::ffff:192.168.12.8",
        "fd12::1%en0",
    ],
)
def test_does_not_automatically_publish_other_private_or_unroutable_addresses(address):
    assert (
        network._parse_candidates(hardware(("Wi-Fi", "en0")), interface(addresses=(address,))) == []
    )


@pytest.mark.parametrize("flag", ["tentative", "duplicated", "detached", "deprecated", "temporary"])
def test_excludes_unready_or_temporary_ipv6_addresses(flag):
    assert (
        network._parse_candidates(
            hardware(("Wi-Fi", "en0")), interface(addresses=(f"fd12::1 prefixlen 64 {flag}",))
        )
        == []
    )


def test_canonicalizes_ipv6_and_deduplicates_repeated_address_lines():
    assert network._parse_candidates(
        hardware(("Wi-Fi", "en0")),
        interface(addresses=("FD12:0000::1 prefixlen 64 autoconf secured", "fd12::1")),
    ) == [network.AddressCandidate("en0", "fd12::1")]


def test_multihoming_is_explicit_and_can_be_followed_by_interface_and_family(commands):
    responses, _ = commands
    responses[("/usr/sbin/networksetup", "-listallhardwareports")] = hardware(
        ("Wi-Fi", "en0"), ("USB Ethernet", "en5")
    )
    responses[("/sbin/ifconfig", "-a")] = interface(
        addresses=("192.168.12.8", "fd12::1")
    ) + interface("en5", ("10.0.1.8",))
    assert network.active_candidates() == [
        network.AddressCandidate("en0", "192.168.12.8"),
        network.AddressCandidate("en0", "fd12::1"),
        network.AddressCandidate("en5", "10.0.1.8"),
    ]
    assert network.active_addresses() == ["192.168.12.8", "fd12::1", "10.0.1.8"]
    with pytest.raises(ValueError, match="Multiple.*en0=192.168.12.8.*en5=10.0.1.8.*--address"):
        network.detect_address()
    with pytest.raises(ValueError, match="Multiple.*--address"):
        network.detect_address(interface="en0")
    assert network.detect_address(interface="en0", family=4) == "192.168.12.8"
    assert network.detect_address(interface="en0", family=6) == "fd12::1"
    assert network.detect_address(interface="en5") == "10.0.1.8"
    with pytest.raises(ValueError, match="No active.*--address"):
        network.detect_address(interface="en5", family=6)
    # A remembered interface never silently switches to the other live port.
    responses[("/sbin/ifconfig", "-a")] = interface(status="inactive") + interface(
        "en5", ("10.0.1.9",)
    )
    with pytest.raises(ValueError, match="No active.*--address"):
        network.detect_address(interface="en0", family=4)
    assert network.detect_address(interface="en5", family=4) == "10.0.1.9"


def test_identical_address_on_two_ports_still_requires_interface_selection(commands):
    responses, _ = commands
    responses[("/usr/sbin/networksetup", "-listallhardwareports")] = hardware(
        ("Wi-Fi", "en0"), ("Ethernet", "en1")
    )
    responses[("/sbin/ifconfig", "-a")] = interface() + interface("en1")
    assert network.active_addresses() == ["192.168.12.8"]
    with pytest.raises(ValueError, match="Multiple"):
        network.detect_address()


def test_multiple_aliases_on_the_same_interface_and_family_remain_ambiguous(commands):
    commands[0][("/sbin/ifconfig", "-a")] = interface(addresses=("10.0.0.8", "10.0.0.9"))
    with pytest.raises(ValueError, match="Multiple.*--address"):
        network.detect_address(interface="en0", family=4)


def test_zero_candidates_has_clear_override_guidance(commands):
    commands[0][("/sbin/ifconfig", "-a")] = interface(status="inactive")
    assert network.active_candidates() == []
    with pytest.raises(ValueError, match="No active private Wi-Fi or Ethernet.*--address"):
        network.detect_address()


@pytest.mark.parametrize(
    "options", [{"interface": "utun0"}, {"interface": "en0;id"}, {"family": 5}]
)
def test_rejects_invalid_selection_before_reading_network_settings(options, commands):
    with pytest.raises(ValueError):
        network.detect_address(**options)
    assert commands[1] == []


@pytest.mark.parametrize(
    ("inventory", "states", "message"),
    [
        ("unexpected output", interface(), "Unrecognized.*hardware"),
        ("Hardware Port: Wi-Fi\n", interface(), "Incomplete.*hardware"),
        (hardware(("Wi-Fi", "en0")), "unexpected output", "Unrecognized.*interface"),
        (
            hardware(("Wi-Fi", "en0")),
            interface() + "utun0: unexpected header\n\tinet 10.0.0.1\n",
            "Unrecognized.*interface",
        ),
        (hardware(("Wi-Fi", "en0")), interface() + interface(), "Duplicate.*interface"),
        (hardware(("Wi-Fi", "en0")), interface(addresses=("bad-ip",)), "Invalid.*address"),
        (hardware(("Wi-Fi", "en0")), interface().replace("inet ", "inet6 "), "Invalid.*family"),
    ],
)
def test_unknown_or_corrupt_command_output_does_not_guess(inventory, states, message):
    with pytest.raises(ValueError, match=message):
        network._parse_candidates(inventory, states)


@pytest.mark.parametrize(
    "error",
    [
        OSError("synthetic"),
        subprocess.CalledProcessError(1, "synthetic"),
        subprocess.TimeoutExpired("synthetic", 5),
    ],
)
def test_command_failures_are_actionable_and_have_no_fallback(error, commands):
    commands[0][("/usr/sbin/networksetup", "-listallhardwareports")] = error
    with pytest.raises(ValueError, match="Could not read macOS network settings; use --address"):
        network.detect_address()
    assert len(commands[1]) == 1


def test_unsupported_platform_never_runs_commands(monkeypatch, commands):
    monkeypatch.setattr(network.sys, "platform", "linux")
    for operation in (network.detect_address, network.active_candidates, network.local_hostname):
        with pytest.raises(ValueError, match="requires macOS; use --address"):
            operation()
    assert commands[1] == []


@pytest.mark.parametrize("label", ["Test-Mac", "x", "3", "a" * 63])
def test_local_hostname_is_only_validated_scutil_name(label, commands):
    commands[0][("/usr/sbin/scutil", "--get", "LocalHostName")] = label + "\n"
    assert network.local_hostname() == label.lower() + ".local"
    assert commands[1] == [("/usr/sbin/scutil", "--get", "LocalHostName")]


@pytest.mark.parametrize(
    "label",
    [
        "",
        "-Mac",
        "Mac-",
        "Mac.local",
        "Test Mac",
        "Mac\nOther",
        " Mac",
        "Mac ",
        "Mac_1",
        "Mác",
        "a" * 64,
        "https://Mac",
        "Mac:4430",
    ],
)
def test_local_hostname_rejects_unset_or_non_label_values(label, commands):
    commands[0][("/usr/sbin/scutil", "--get", "LocalHostName")] = label + "\n"
    with pytest.raises(ValueError, match="no valid Bonjour local hostname; use --address"):
        network.local_hostname()


def test_unset_scutil_hostname_does_not_fall_back_to_dns(commands):
    commands[0][("/usr/sbin/scutil", "--get", "LocalHostName")] = subprocess.CalledProcessError(
        1, "scutil"
    )
    with pytest.raises(ValueError, match="Could not read macOS network settings"):
        network.local_hostname()
    assert commands[1] == [("/usr/sbin/scutil", "--get", "LocalHostName")]
