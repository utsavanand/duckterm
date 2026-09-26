"""The broker pins a secret version and rejects arbitrary URL/path references."""

import pytest

from duckterm.helpers.secret_store import GcpSecretStore


@pytest.mark.parametrize(
    "reference",
    [
        "https://example.com/steal",
        "projects/p/secrets/x/versions/latest",
        "projects/p/secrets/../versions/1",
        "projects/p/secrets/x/versions/0",
    ],
)
def test_unpinned_or_untrusted_reference_is_rejected(reference: str) -> None:
    with pytest.raises(ValueError, match="numeric"):
        GcpSecretStore().read(reference)
