"""zsh prompt themes: listing from ~/.oh-my-zsh and the ZDOTDIR wrapper that
swaps the theme even though the user's ~/.zshrc hardcodes ZSH_THEME."""

import shutil
import subprocess
from pathlib import Path

import pytest

from duckterm import zsh_themes


def _fake_omz(home: Path, stock: list[str], custom: list[str] | None = None) -> None:
    themes = home / ".oh-my-zsh" / "themes"
    themes.mkdir(parents=True)
    for name in stock:
        (themes / f"{name}.zsh-theme").write_text(f'DUCKTERM_THEME_MARK="{name}"\n')
    if custom:
        cdir = home / ".oh-my-zsh" / "custom" / "themes"
        cdir.mkdir(parents=True)
        for name in custom:
            (cdir / f"{name}.zsh-theme").write_text(f'DUCKTERM_THEME_MARK="{name}"\n')


def test_list_themes_reads_stock_and_custom_dirs(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("HOME", str(tmp_path))
    assert zsh_themes.list_themes() == []  # no oh-my-zsh at all
    _fake_omz(tmp_path, ["agnoster", "robbyrussell"], custom=["mine"])
    assert zsh_themes.list_themes() == ["agnoster", "mine", "robbyrussell"]


def test_theme_env_rejects_names_that_are_not_installed(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _fake_omz(tmp_path, ["agnoster"])
    with pytest.raises(ValueError, match="unknown zsh theme"):
        zsh_themes.theme_env("../../../etc")


@pytest.mark.skipif(shutil.which("zsh") is None, reason="needs zsh")
def test_wrapper_swaps_theme_in_a_real_zsh_despite_hardcoded_zshrc(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    """The whole point of the wrapper: the user's zshrc hardcodes
    ZSH_THEME="robbyrussell", yet the launched shell ends up with the chosen
    theme file sourced AND the user's own rc still applied."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    _fake_omz(home, ["robbyrussell"], custom=["ducky"])
    (home / ".zshrc").write_text(
        'export ZSH="$HOME/.oh-my-zsh"\nZSH_THEME="robbyrussell"\nexport RC_RAN=yes\n'
    )

    env = zsh_themes.theme_env("ducky")
    wrapper = Path(env["ZDOTDIR"])
    assert (wrapper / ".zshrc").is_file()

    out = subprocess.run(
        ["zsh", "-i", "-c", 'echo "MARK=$DUCKTERM_THEME_MARK RC=$RC_RAN THEME=$ZSH_THEME"'],
        env={"HOME": str(home), "PATH": "/usr/bin:/bin", **env},
        capture_output=True,
        text=True,
        timeout=15,
    ).stdout
    assert "MARK=ducky" in out  # the chosen theme file was sourced…
    assert "RC=yes" in out  # …after the user's real zshrc ran
    assert "THEME=ducky" in out


def test_launch_with_unknown_zsh_theme_is_a_400(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The launch endpoint fails loudly (400 + reason) instead of silently
    starting the session without the requested theme."""
    import asyncio
    import json
    import urllib.error
    import urllib.request

    from duckterm.helpers import security
    from duckterm.persistence.history import HistoryStore
    from duckterm.server import Server

    monkeypatch.setenv("HOME", str(tmp_path))

    def _post(port: int) -> tuple[int, str]:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/sessions/launch",
            data=json.dumps(
                {
                    "command": "sh -c 'true'",
                    "cwd": str(tmp_path),
                    "in_terminal": False,
                    "zsh_theme": "no-such-theme",
                    "test": True,
                }
            ).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Duckterm-Token": security.load_or_create_token(),
            },
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            return 200, ""
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    async def scenario() -> tuple[int, str]:
        store = HistoryStore(tmp_path / "db.sqlite")
        srv = await asyncio.start_server(Server(history=store).handle, "127.0.0.1", 0)
        port = srv.sockets[0].getsockname()[1]
        async with srv:
            return await asyncio.to_thread(_post, port)

    status, body = asyncio.run(scenario())
    assert status == 400
    assert "unknown zsh theme" in body
