"""
Simmer SDK — Risk primitives.

Defensive detectors that gate automated MM/copytrade skills.
These are halt signals only — never buy/trade signals.
"""

from .oracle_engineering import OracleEngineeringDetector, RiskFlag, RiskLevel

__all__ = ["OracleEngineeringDetector", "RiskFlag", "RiskLevel"]
