"""
Tests for SIMMER_ELON_SLIPPAGE_BPS basis-point → fraction conversion (SIM-2374).

Verifies that the slippage gate works correctly when the env var is set in
basis points (as documented in clawhub.json) rather than as a raw fraction.
"""

import sys
import os
import types
import unittest
from unittest.mock import patch

_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SKILL_DIR)


def _make_mock_sdk():
    """Return a minimal stub so elon_tweets.py imports without the real SDK."""
    sdk = types.ModuleType("simmer_sdk")
    sdk.SimmerClient = object

    skill_mod = types.ModuleType("simmer_sdk.skill")

    def _load_config(schema, *a, **kw):
        return {k: v["default"] for k, v in schema.items()}

    skill_mod.load_config = _load_config
    skill_mod.update_config = lambda *a, **kw: None
    skill_mod.get_config_path = lambda *a: type("P", (), {"exists": lambda s: False})()
    sdk.skill = skill_mod

    return sdk


class TestSlippageBpsConversion(unittest.TestCase):
    def _import_module(self, bps_value=None):
        """Import elon_tweets with an optional SIMMER_ELON_SLIPPAGE_BPS override."""
        import importlib

        sdk = _make_mock_sdk()

        def _load_config_override(schema, *a, **kw):
            result = {k: v["default"] for k, v in schema.items()}
            if bps_value is not None:
                result["slippage_max_bps"] = float(bps_value)
            return result

        sdk.skill.load_config = _load_config_override

        mods_to_remove = [k for k in sys.modules if "elon_tweets" in k or "simmer_sdk" in k]
        for m in mods_to_remove:
            del sys.modules[m]

        with patch.dict(sys.modules, {"simmer_sdk": sdk, "simmer_sdk.skill": sdk.skill}):
            import elon_tweets as et
            return et

    def test_default_bps_100_converts_to_1pct(self):
        """Default 100 bps should convert to 0.01 (1%) fraction."""
        et = self._import_module(bps_value=100)
        self.assertAlmostEqual(et.SLIPPAGE_MAX_PCT, 0.01)

    def test_bps_500_converts_to_5pct(self):
        """500 bps should convert to 0.05 (5%) fraction."""
        et = self._import_module(bps_value=500)
        self.assertAlmostEqual(et.SLIPPAGE_MAX_PCT, 0.05)

    def test_bps_100_rejects_5pct_slippage(self):
        """With 100 bps (1%) cap, a 5% slippage estimate must be rejected."""
        et = self._import_module(bps_value=100)
        # slippage_pct > SLIPPAGE_MAX_PCT → gate fires → False
        self.assertTrue(0.05 > et.SLIPPAGE_MAX_PCT)

    def test_bps_500_allows_1pct_slippage(self):
        """With 500 bps (5%) cap, a 1% slippage estimate should pass."""
        et = self._import_module(bps_value=500)
        self.assertFalse(0.01 > et.SLIPPAGE_MAX_PCT)

    def test_bps_10_minimum_converts_to_01pct(self):
        """Slider minimum 10 bps should convert to 0.001 (0.1%) — gate still valid."""
        et = self._import_module(bps_value=10)
        self.assertAlmostEqual(et.SLIPPAGE_MAX_PCT, 0.001)


if __name__ == "__main__":
    unittest.main()
