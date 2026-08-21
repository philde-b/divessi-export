from unittest import mock

import requests_mock

from divessi.api import SSIApi
from divessi.constants import BASE_URL
from divessi.discover import discover, looks_empty, probe
from divessi.exceptions import TimeoutException


def _api():
    with mock.patch("divessi.api.SSIApi._authenticate", return_value="token"):
        return SSIApi("email", "password")


def test_looks_empty():
    assert looks_empty(None)
    assert looks_empty([])
    assert looks_empty({})
    assert looks_empty("")
    assert looks_empty({"error": "unknown command"})
    assert looks_empty({"status": 0, "data": []})
    assert not looks_empty({"a": 1})
    assert not looks_empty([1])
    assert not looks_empty({"status": "ok", "items": [1]})


def test_probe_classifies_data_empty_and_error():
    api = _api()

    with requests_mock.Mocker() as m:
        m.get(BASE_URL, json={"certs": [1]})
        assert probe(api, "get_certs")["status"] == "data"

        m.get(BASE_URL, json={})
        assert probe(api, "get_nothing")["status"] == "empty"

        m.get(BASE_URL, status_code=500, json={})
        assert probe(api, "get_broken")["status"] == "error"

        m.get(BASE_URL, status_code=404, json=[])
        assert probe(api, "get_made_up")["status"] == "unknown"


def test_probe_classifies_timeout():
    api = _api()
    with mock.patch.object(api, "get_raw", side_effect=TimeoutException("slow")):
        assert probe(api, "get_slow")["status"] == "timeout"


def test_discover_cools_down_once_and_retries_the_streak(monkeypatch):
    api = _api()
    sleeps = []
    monkeypatch.setattr("divessi.discover.time.sleep", lambda s: sleeps.append(s))
    answers = [
        {"a": 1},  # a
        TimeoutException("t"),  # b
        TimeoutException("t"),  # c: second in a row, cool down, retry from b
        {"b": 1},  # b again
        {"c": 1},  # c again
        {"d": 1},  # d
    ]

    with mock.patch.object(api, "get_raw", side_effect=answers):
        results = discover(api, ["a", "b", "c", "d"], delay=0, cooldown=60)

    assert [r["status"] for r in results.values()] == ["data"] * 4
    assert sleeps == [60]


def test_discover_stops_when_timeouts_persist_after_cooldown(monkeypatch):
    api = _api()
    sleeps = []
    monkeypatch.setattr("divessi.discover.time.sleep", lambda s: sleeps.append(s))

    with mock.patch.object(api, "get_raw", side_effect=TimeoutException("t")) as fake:
        results = discover(api, ["a", "b", "c", "d"], delay=0, cooldown=60)

    assert [r["status"] for r in results.values()] == ["skipped"] * 4
    assert all(r["detail"] == "rate limited" for r in results.values())
    assert sleeps == [60]
    # a, b, then a, b again after the cooldown; c and d never attempted
    assert fake.call_count == 4


def test_discover_stops_when_token_is_rejected():
    api = _api()
    seen = []
    calls = {"n": 0}

    def responder(request, context):
        calls["n"] += 1
        if calls["n"] == 2:
            return {"status": 401, "authenticated": False, "message": "dead"}
        return {"x": 1}

    with requests_mock.Mocker() as m:
        m.get(BASE_URL, json=responder)
        results = discover(
            api,
            ["a", "b", "c"],
            delay=0,
            on_result=lambda r: seen.append(r["status"]),
        )

    assert [r["status"] for r in results.values()] == ["data", "skipped", "skipped"]
    assert seen == ["data", "skipped", "skipped"]
    assert calls["n"] == 2


def test_discover_returns_every_candidate_and_reports():
    api = _api()
    seen = []

    with requests_mock.Mocker() as m:
        m.get(BASE_URL, json={"x": 1})
        results = discover(
            api, ["a", "b", "c"], delay=0, on_result=lambda r: seen.append(r["what"])
        )

    assert list(results) == ["a", "b", "c"]
    assert seen == ["a", "b", "c"]
    assert all(r["status"] == "data" for r in results.values())


def test_discover_accepts_generator():
    api = _api()

    with requests_mock.Mocker() as m:
        m.get(BASE_URL, json={"x": 1})
        results = discover(api, (w for w in ["a", "b"]), delay=0)

    assert list(results) == ["a", "b"]
