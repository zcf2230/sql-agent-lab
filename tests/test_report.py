"""Cost reporting tests.

A stale price table once made this project report ¥23 for ¥10 of real spend. The
lesson is encoded here rather than in a commit message: a derived figure must not
be stored and then displayed as though it were an observation.
"""

from __future__ import annotations

from sqlagent.config import PRICING
from sqlagent.report import token_cost


def rows(n_in: int, n_out: int, stored: float) -> list[dict]:
    return [
        {"id": "a", "stats": {"prompt_tokens": n_in // 2, "completion_tokens": n_out // 2}},
        {"id": "b", "stats": {"prompt_tokens": n_in - n_in // 2, "completion_tokens": n_out - n_out // 2},
         "total_cost_usd": stored},
    ]


def test_cost_is_computed_from_tokens_not_from_the_stored_figure():
    pin, pout = PRICING["deepseek-chat"]
    got = token_cost(rows(1_000_000, 100_000, stored=999.0), "deepseek-chat")
    assert abs(got - (1_000_000 * pin + 100_000 * pout) / 1e6) < 1e-9
    assert got != 999.0, "a frozen cost figure leaked into the display"


def test_unknown_model_costs_nothing_rather_than_guessing():
    assert token_cost(rows(500_000, 10_000, stored=5.0), "some-model-not-in-the-table") == 0.0


def test_price_table_is_usd_per_million_sized():
    """Guards the unit confusion that produced the 2.3x overstatement."""
    for model, (pin, pout) in PRICING.items():
        if pin == 0.0:
            continue
        assert 0.0 < pin < 10.0, f"{model}: input price looks like per-token, not per-million"
        assert 0.0 < pout < 40.0, f"{model}: output price looks like per-token, not per-million"
