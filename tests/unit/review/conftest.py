import pytest

from duckterm.review import git


@pytest.fixture(autouse=True)
def local_remotes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture remotes are folders; DuckTerm itself only fetches over https and ssh."""
    monkeypatch.setattr(git, "_REMOTE_PROTOCOLS", ("https", "ssh", "file"))
