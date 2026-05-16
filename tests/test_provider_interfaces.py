from __future__ import annotations

import pytest

from src.data.providers.adapters import IBKRAdapter, JQuantsHistoricalAdapter, KabuStationAdapter, TachibanaAdapter


def test_broker_adapters_are_safe_stubs_until_real_credentials_and_order_code_exist():
    assert JQuantsHistoricalAdapter.name == "jquants_historical"
    for adapter_type in (TachibanaAdapter, KabuStationAdapter, IBKRAdapter):
        adapter = adapter_type({})
        adapter.subscribe_quotes(["1321"])
        assert adapter.get_latest_quote("1321") is None
        with pytest.raises(RuntimeError):
            adapter.place_order("1321", "BUY", 1, "market")

