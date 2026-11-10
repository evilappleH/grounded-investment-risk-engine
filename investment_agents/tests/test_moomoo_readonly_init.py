import os
from unittest.mock import patch

from investment_agents.integrations.moomoo.official import OfficialMoomooTransport


def test_moomoo_sdk_import_uses_writable_project_log_path_without_order_calls(tmp_path):
    observed = {}

    class FakeImport:
        def import_module(self, name):
            observed["name"] = name
            observed["appdata"] = os.environ.get("appdata", "")
            return object()

    with patch("importlib.import_module", new=FakeImport().import_module):
        sdk = OfficialMoomooTransport._import_sdk_with_writable_log()
    assert sdk is not None
    assert observed["name"] == "moomoo"
    assert observed["appdata"].endswith("data_cache\\moomoo_sdk") or observed["appdata"].endswith("data_cache/moomoo_sdk")
