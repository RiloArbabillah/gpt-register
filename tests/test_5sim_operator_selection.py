"""Operator selection and stock-aware acquisition for 5sim."""
from __future__ import annotations

import json

import pytest

import sms_provider as sp


class FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload

    def raise_for_status(self):
        return None


def provider(tmp_path, monkeypatch, *, max_price=-1):
    monkeypatch.setenv("WEBUI_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sp, "_FIVESIM_CACHE", None)
    return sp.FiveSimProvider("key", max_price=max_price)


def nested_prices(**operators):
    return {"thailand": {"openai": operators}}


def test_nested_prices_are_normalized_and_sorted(monkeypatch, tmp_path):
    p = provider(tmp_path, monkeypatch)
    p._request = lambda path, **kwargs: FakeResponse(nested_prices(
        slow={"cost": "0.03", "count": "8", "rate": "12"},
        fast={"cost": "0.05", "count": "2", "rate": "23"},
    ))

    assert p._operator_candidates("thailand", "openai") == [
        {"operator": "fast", "cost": 0.05, "count": 2, "rate": 23},
        {"operator": "slow", "cost": 0.03, "count": 8, "rate": 12},
    ]


def test_zero_stock_and_overpriced_operators_are_excluded(monkeypatch, tmp_path):
    p = provider(tmp_path, monkeypatch, max_price=0.10)
    p._request = lambda path, **kwargs: FakeResponse(nested_prices(
        empty={"cost": 0.01, "count": 0, "rate": 100},
        expensive={"cost": 0.11, "count": 20, "rate": 99},
        usable={"cost": 0.10, "count": 1, "rate": 1},
    ))

    assert [row["operator"] for row in p._operator_candidates("thailand", "openai")] == ["usable"]


def test_ties_use_cost_then_count_then_operator(monkeypatch, tmp_path):
    p = provider(tmp_path, monkeypatch)
    p._request = lambda path, **kwargs: FakeResponse(nested_prices(
        zeta={"cost": 0.04, "count": 3, "rate": 10},
        alpha={"cost": 0.04, "count": 3, "rate": 10},
        beta={"cost": 0.03, "count": 1, "rate": 10},
        gamma={"cost": 0.04, "count": 5, "rate": 10},
    ))

    assert [row["operator"] for row in p._operator_candidates("thailand", "openai")] == [
        "beta", "gamma", "alpha", "zeta"
    ]


def test_non_positive_max_price_does_not_filter(monkeypatch, tmp_path):
    p = provider(tmp_path, monkeypatch, max_price=0)
    p._request = lambda path, **kwargs: FakeResponse(nested_prices(
        available={"cost": 999, "count": 1, "rate": 1},
    ))

    assert len(p._operator_candidates("thailand", "openai")) == 1


def test_buy_uses_ranked_operator_and_falls_back_after_failure(monkeypatch, tmp_path):
    p = provider(tmp_path, monkeypatch)
    calls = []
    p._request = lambda path, **kwargs: calls.append((path, kwargs)) or (
        FakeResponse(nested_prices(
            first={"cost": 0.01, "count": 5, "rate": 20},
            second={"cost": 0.02, "count": 5, "rate": 10},
        )) if path == "guest/prices" else FakeResponse(
            {"id": "42", "phone": "6612345678", "expires": "later"}
        ) if path.endswith("/second/openai") else (_ for _ in ()).throw(
            RuntimeError("sold out")
        )
    )

    activation = p.get_number(service="openai", country="thailand")

    assert activation.activation_id == "42"
    assert activation.metadata["operator"] == "second"
    assert [call[0] for call in calls] == [
        "guest/prices", "user/buy/activation/thailand/first/openai",
        "user/buy/activation/thailand/second/openai",
    ]


def test_all_operator_failures_include_each_reason(monkeypatch, tmp_path):
    p = provider(tmp_path, monkeypatch)
    p._request = lambda path, **kwargs: FakeResponse(nested_prices(
        alpha={"cost": 0.01, "count": 2, "rate": 2},
        beta={"cost": 0.02, "count": 2, "rate": 1},
    )) if path == "guest/prices" else (_ for _ in ()).throw(
        RuntimeError("provider rejected")
    )

    with pytest.raises(RuntimeError, match=r"alpha.*provider rejected.*beta.*provider rejected"):
        p.get_number(service="openai", country="thailand")


def test_activation_metadata_and_old_cache_are_compatible(monkeypatch, tmp_path):
    p = provider(tmp_path, monkeypatch)
    old_cache = {
        **p._cache_identity("openai", "thailand"),
        "activation_id": "old", "phone_number": "+6612345678",
        "acquired_at": 1, "expires_at": 9999999999, "use_count": 0,
        "used_codes": [], "reuse_stopped": False, "cooldown_until": 0,
    }
    p._save_cache(old_cache)
    p.get_status = lambda aid: {"status": "wait_code"}

    activation = p.get_number(service="openai", country="thailand")

    assert activation.activation_id == "old"
    assert activation.metadata["reused"] is True
    assert "operator" not in activation.metadata


def test_new_activation_metadata_is_persisted(monkeypatch, tmp_path):
    p = provider(tmp_path, monkeypatch)
    p._request = lambda path, **kwargs: FakeResponse(nested_prices(
        virtual34={"cost": 0.0769, "count": 100961, "rate": 23},
    )) if path == "guest/prices" else FakeResponse(
        {"id": "new", "phone": "6612345678", "price": 0.0769}
    )

    activation = p.get_number(service="openai", country="thailand")
    cache = json.loads((tmp_path / ".5sim_phone_cache.json").read_text())

    expected = {"operator": "virtual34", "cost": 0.0769, "rate": 23, "count": 100961}
    assert {key: activation.metadata[key] for key in expected} == expected
    assert {key: cache[key] for key in expected} == expected
