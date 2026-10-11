"""Shared fixtures keeping SDK unit tests independent of the network."""

import pytest


@pytest.fixture(autouse=True)
def stub_server_version_check(monkeypatch, request):
    """Skip constructor version requests; preserve the version checker unit tests."""
    if request.node.path.name == "test_version_check.py":
        return
    monkeypatch.setattr(
        "simmer_sdk.version_check.check_server_version_compatibility",
        lambda *args, **kwargs: None,
    )
