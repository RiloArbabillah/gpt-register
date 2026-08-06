"""Operator selection and stock-aware acquisition for 5sim."""
from __future__ import annotations

import json

import pytest
import requests

import sms_provider as sp


class FakeResponse:
    def __init__(self, payload=None, *, status_code=200, text="", json_error=False):
        self.payload = payload
        self.status_code = status_code
        self.text = text
        self.json_error = json_error

    def json(self):
        if self.json_error:
            raise ValueError("invalid JSON")
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


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


def test_purchase_200_no_free_phones_falls_back_without_retry(monkeypatch, tmp_path):
    p = provider(tmp_path, monkeypatch)
    calls = []

    def request(path, **kwargs):
        calls.append((path, kwargs))
        if path == "guest/prices":
            return FakeResponse(nested_prices(
                first={"cost": 0.01, "count": 5, "rate": 20},
                second={"cost": 0.02, "count": 5, "rate": 10},
            ))
        if path.endswith("/first/openai"):
            return FakeResponse(text="no free phones", json_error=True)
        return FakeResponse({"id": "42", "phone": "6612345678"})

    p._request = request

    activation = p.get_number(service="openai", country="thailand")

    assert activation.activation_id == "42"
    purchases = [call for call in calls if "/user/buy/" in call[0]]
    assert [call[0] for call in purchases] == [
        "user/buy/activation/thailand/first/openai",
        "user/buy/activation/thailand/second/openai",
    ]
    assert all(call[1]["params"] == {"reuse": 1} for call in purchases)


def test_purchase_200_empty_body_reports_provider_error_without_json_decode_message(
    monkeypatch, tmp_path
):
    p = provider(tmp_path, monkeypatch)
    p._request = lambda path, **kwargs: (
        FakeResponse(nested_prices(alpha={"cost": 0.01, "count": 1, "rate": 1}))
        if path == "guest/prices"
        else FakeResponse(text="", json_error=True)
    )

    with pytest.raises(RuntimeError) as exc_info:
        p.get_number(service="openai", country="thailand")

    message = str(exc_info.value)
    assert "thailand/alpha" in message
    assert "provider error" in message
    assert 'HTTP 200; body=""' in message
    assert "Expecting value" not in message


def test_purchase_http_errors_preserve_status_and_body(monkeypatch, tmp_path):
    p = provider(tmp_path, monkeypatch)
    responses = {
        "alpha": FakeResponse(status_code=400, text="not enough user balance"),
        "beta": FakeResponse(status_code=500, text="internal error"),
    }
    calls = []

    def request(path, **kwargs):
        calls.append(path)
        if path == "guest/prices":
            return FakeResponse(nested_prices(
                alpha={"cost": 0.01, "count": 1, "rate": 2},
                beta={"cost": 0.02, "count": 1, "rate": 1},
            ))
        operator = path.split("/")[-2]
        return responses[operator]

    p._request = request

    with pytest.raises(RuntimeError) as exc_info:
        p.get_number(service="openai", country="thailand")

    message = str(exc_info.value)
    assert 'thailand/alpha: provider error (HTTP 400; body="not enough user balance")' in message
    assert 'thailand/beta: provider error (HTTP 500; body="internal error")' in message
    assert calls.count("user/buy/activation/thailand/alpha/openai") == 1
    assert calls.count("user/buy/activation/thailand/beta/openai") == 1
