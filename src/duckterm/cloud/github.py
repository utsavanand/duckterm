"""The three GitHub OAuth calls the account service makes, with urllib.

The OAuth app requests no scopes, so a token only proves who the user is. The
service revokes each token as soon as it knows the user.
"""

from __future__ import annotations

import base64
import contextlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GitHubUser:
    id: int
    login: str


class GitHubError(Exception):
    pass


class GitHub:
    def __init__(self, client_id: str, client_secret: str) -> None:
        self.client_id = client_id
        self.client_secret = client_secret

    def authorize_url(self, redirect_uri: str, state: str) -> str:
        from urllib.parse import urlencode

        query = urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "allow_signup": "true",
            }
        )
        return "https://github.com/login/oauth/authorize?" + query

    def exchange(self, code: str, redirect_uri: str) -> str:
        """A user token for a web-flow code; GitHubError when GitHub refuses it."""
        answer = self._call(
            "POST",
            "https://github.com/login/oauth/access_token",
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
        token = answer.get("access_token")
        if not isinstance(token, str):
            raise GitHubError("GitHub did not accept the sign-in code")
        return token

    def user(self, token: str) -> GitHubUser:
        """The user behind a token issued to this app, never another app's token."""
        answer = self._call(
            "POST",
            f"https://api.github.com/applications/{self.client_id}/token",
            {"access_token": token},
            app=True,
        )
        user = answer.get("user")
        if not isinstance(user, dict):
            raise GitHubError("GitHub token has no user")
        identifier, login = user.get("id"), user.get("login")
        if type(identifier) is not int or not isinstance(login, str):
            raise GitHubError("GitHub token has no user")
        return GitHubUser(identifier, login)

    def revoke(self, token: str) -> None:
        # A failed revoke leaves a scope-less token that expires on its own.
        with contextlib.suppress(GitHubError):
            self._call(
                "DELETE",
                f"https://api.github.com/applications/{self.client_id}/token",
                {"access_token": token},
                app=True,
            )

    def _call(
        self, method: str, url: str, body: dict[str, Any], *, app: bool = False
    ) -> dict[str, Any]:
        request = urllib.request.Request(url, data=json.dumps(body).encode(), method=method)
        request.add_header("Accept", "application/json")
        request.add_header("Content-Type", "application/json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        if app:
            pair = f"{self.client_id}:{self.client_secret}".encode()
            request.add_header("Authorization", "Basic " + base64.b64encode(pair).decode())
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read(65536)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GitHubError("GitHub is unavailable or refused the request") from exc
        if not raw:
            return {}
        answer = json.loads(raw)
        if not isinstance(answer, dict):
            raise GitHubError("Unexpected GitHub answer")
        return answer
