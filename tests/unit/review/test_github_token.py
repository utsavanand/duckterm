"""The credential review uses for pull request status, and what gates it."""

from duckterm import connectors
from duckterm.review import github


def test_withdrawing_from_agents_leaves_pull_request_status_working(monkeypatch):
    """Off means off for agents only -- Duckterm keeps using the credential.

    This gate is why Disable blanked the PR list and asked the owner to enable
    a connector they had just turned off. The flag is about agents; reading
    pull request status is Duckterm's own use of the owner's token.
    """
    monkeypatch.setattr(connectors, "policy", lambda _: {"enabled": False, "source": "gh-cli"})
    monkeypatch.setattr(connectors, "github_token", lambda _: ("ghp_owner", "gh-cli"))
    assert github.token() == ("ghp_owner", None)


def test_missing_credential_still_reports_why(monkeypatch):
    """Dropping the policy gate must not drop the credential check with it."""
    monkeypatch.setattr(connectors, "policy", lambda _: {"enabled": True, "source": "gh-cli"})
    monkeypatch.setattr(connectors, "github_token", lambda _: None)
    assert github.token() == (None, "Reconnect the GitHub connector to see pull request status")
