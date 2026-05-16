from __future__ import annotations

from src.config.loader import load_project_config
from src.data.symbol_mapper import SymbolMapper


def test_symbol_mapper_reads_enabled_symbols_from_config():
    config = load_project_config()
    mapper = SymbolMapper(config)
    instruments = mapper.enabled_instruments()
    assert {item.action for item in instruments} == {"long_1x", "long_2x", "short_1x", "short_2x"}
    assert mapper.select_for_action("long_2x").symbol == "1570"
    assert mapper.provider_symbol("long_2x", "1570", "jquants") == "15700"

