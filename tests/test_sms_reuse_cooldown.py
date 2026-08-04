"""Cooldown semantics for reusable SMS numbers (SmsBower / 5sim).

A successfully-used number must wait out the provider cooldown (3-5 minutes,
default 240s) before the same activation/order is reused.
"""
from __future__ import annotations

import time

import pytest

import sms_provider as sp


@pytest.fixture()
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBUI_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sp, "_SMS_CACHE", None)
    monkeypatch.setattr(sp, "_FIVESIM_CACHE", None)
    yield tmp_path


def _bower(**overrides):
    kwargs = {
        "reuse_phone_to_max": True,
        "phone_success_max": 5,
        "reuse_cooldown_seconds": 240,
    }
    kwargs.update(overrides)
    return sp.SmsBowerProvider(api_key="test-key", **kwargs)


def _fivesim(**overrides):
    kwargs = {
        "reuse_phone_to_max": True,
        "phone_success_max": 5,
        "reuse_cooldown_seconds": 240,
    }
    kwargs.update(overrides)
    return sp.FiveSimProvider(api_key="test-key", **kwargs)


def _bower_cache(provider: sp.SmsBowerProvider, *, cooldown_until: float = 0) -> dict:
    cache = {
        **provider._cache_identity("dr", "52"),
        "country": "52",
        "activation_id": "111",
        "phone_number": "+6612345678",
        "acquired_at": time.time(),
        "use_count": 1,
        "used_codes": set(),
        "reuse_stopped": False,
        "stop_reason": "",
        "cooldown_until": cooldown_until,
    }
    provider._save_cache(cache)
    return cache


def _fivesim_cache(provider: sp.FiveSimProvider, *, cooldown_until: float = 0) -> dict:
    cache = {
        **provider._cache_identity("openai", "thailand"),
        "activation_id": "222",
        "phone_number": "+6612345678",
        "acquired_at": time.time(),
        "expires_at": time.time() + 1200,
        "use_count": 1,
        "used_codes": set(),
        "reuse_stopped": False,
        "stop_reason": "",
        "cooldown_until": cooldown_until,
    }
    provider._save_cache(cache)
    return cache


def test_smsbower_reuse_waits_for_cooldown(isolated_cache, monkeypatch):
    provider = _bower()
    _bower_cache(provider, cooldown_until=time.time() + 240)
    slept: list[float] = []
    monkeypatch.setattr(sp.time, "sleep", lambda s: slept.append(s))

    activation = provider.get_number(service="dr", country="52")

    assert activation.metadata["reused"] is True
    assert activation.activation_id == "111"
    assert slept and 230 <= slept[0] <= 240


def test_smsbower_reuse_without_cooldown_does_not_wait(isolated_cache, monkeypatch):
    provider = _bower()
    _bower_cache(provider, cooldown_until=0)
    monkeypatch.setattr(sp.time, "sleep", lambda s: pytest.fail(f"unexpected sleep {s}"))

    activation = provider.get_number(service="dr", country="52")

    assert activation.metadata["reused"] is True


def test_smsbower_report_success_sets_cooldown(isolated_cache):
    provider = _bower()
    _bower_cache(provider)

    assert provider.report_success("111") is True

    cache = provider._load_cache("dr", "52")
    remaining = cache["cooldown_until"] - time.time()
    assert 230 <= remaining <= 240


def test_smsbower_report_success_at_max_skips_cooldown(isolated_cache):
    provider = _bower(phone_success_max=2)
    cache = _bower_cache(provider)
    cache["use_count"] = 1
    provider._save_cache(cache)
    provider._request = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("finishActivation stub should be the only network call")
    )

    # finishActivation path runs; stub the request to succeed without network.
    class _Resp:
        status_code = 200
        text = "ACCESS_FINISH"

    provider._request = lambda *a, **k: _Resp()
    assert provider.report_success("111") is True
    assert provider._load_cache("dr", "52") is None  # reuse_stopped -> cleared on load


def test_fivesim_reuse_waits_for_cooldown(isolated_cache, monkeypatch):
    provider = _fivesim()
    _fivesim_cache(provider, cooldown_until=time.time() + 240)
    provider.get_status = lambda aid: {"status": "wait_code"}
    slept: list[float] = []
    monkeypatch.setattr(sp.time, "sleep", lambda s: slept.append(s))

    activation = provider.get_number(service="openai", country="thailand")

    assert activation.metadata["reused"] is True
    assert activation.activation_id == "222"
    assert slept and 230 <= slept[0] <= 240


def test_fivesim_report_success_sets_cooldown(isolated_cache):
    provider = _fivesim()
    _fivesim_cache(provider)

    assert provider.report_success("222") is True

    cache = provider._load_cache("openai", "thailand")
    remaining = cache["cooldown_until"] - time.time()
    assert 230 <= remaining <= 240


def test_cooldown_config_is_clamped_to_180_300(isolated_cache):
    base = {"sms_api_key": "k", "sms_reuse_phone": "1"}

    low = sp.create_sms_provider("smsbower", {**base, "sms_reuse_cooldown_seconds": "60"})
    high = sp.create_sms_provider("5sim", {**base, "sms_reuse_cooldown_seconds": "999"})
    default = sp.create_sms_provider("smsbower", base)

    assert low.reuse_cooldown_seconds == 180
    assert high.reuse_cooldown_seconds == 300
    assert default.reuse_cooldown_seconds == 240
