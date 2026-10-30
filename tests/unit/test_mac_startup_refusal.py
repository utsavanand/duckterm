"""Keep the CLI's bind refusal compatible with the Mac startup error parser."""

import pytest

from duckterm import cli


@pytest.mark.parametrize("responds", [True, False])
def test_port_refusal_has_machine_readable_reason(monkeypatch, capsys, responds):
    monkeypatch.setattr(cli, "_duckterm_responds", lambda host, port: responds)
    cli._print_port_in_use("127.0.0.1", 4300)
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.splitlines()[0] == "duckterm: Port 4300 is already in use."
    assert ("Another Duckterm is already running" in output.err) == responds
    assert "Inspect the existing process before stopping it" in output.err
