from unittest import mock

import pytest
import requests
import requests_mock

from divessi.api import SSIApi
from divessi.constants import BASE_URL
from divessi.exceptions import (
    ApiException,
    AuthenticationException,
    TimeoutException,
    TokenException,
    UnknownCommandException,
)


def test_authenticate():
    with requests_mock.Mocker() as m:
        m.get(BASE_URL, json={"token": "success"})
        api = SSIApi("email", "password")

    assert api.token == "success"
    assert m.last_request.qs["l"] == ["email"]
    assert m.last_request.qs["p"] == ["password"]
    assert m.last_request.qs["what"] == ["authenticate"]


def test_authenticate_encodes_special_characters():
    """Passwords with reserved URL characters must survive the trip."""
    with requests_mock.Mocker() as m:
        m.get(BASE_URL, json={"token": "success"})
        SSIApi("email", "p&ss w+rd#1")

    assert m.last_request.qs["p"] == ["p&ss w+rd#1"]


def test_authenticate_error():
    with requests_mock.Mocker() as m, pytest.raises(AuthenticationException):
        m.get(BASE_URL, json={})
        SSIApi("email", "password")


def test_authenticate_non_json_body():
    with requests_mock.Mocker() as m, pytest.raises(ApiException):
        m.get(BASE_URL, text="<html>maintenance</html>")
        SSIApi("email", "password")


def test_get_raw_returns_untouched_payload():
    payload = {"anything": [1, 2, 3], "nested": {"x": None}}

    with mock.patch(
        "divessi.api.SSIApi._authenticate", return_value="token"
    ), requests_mock.Mocker() as m:
        m.get(BASE_URL, json=payload)
        api = SSIApi("email", "password")
        result = api.get_raw("get_whatever", extra="1")

    assert result == payload
    assert m.last_request.qs["what"] == ["get_whatever"]
    assert m.last_request.qs["token"] == ["token"]
    assert m.last_request.qs["extra"] == ["1"]


def test_get_raw_http_error():
    with mock.patch(
        "divessi.api.SSIApi._authenticate", return_value="token"
    ), requests_mock.Mocker() as m, pytest.raises(ApiException):
        m.get(BASE_URL, status_code=403, json={})
        SSIApi("email", "password").get_raw("get_divelog")


def test_get_raw_per_call_timeout_overrides_default():
    with mock.patch(
        "divessi.api.SSIApi._authenticate", return_value="token"
    ), requests_mock.Mocker() as m:
        m.get(BASE_URL, json={"x": 1})
        api = SSIApi("email", "password", timeout=30)
        api.get_raw("get_divelog")
        assert m.last_request.timeout == 30
        api.get_raw("get_divelog", timeout=5)
        assert m.last_request.timeout == 5
        # timeout must not leak into the query string
        assert "timeout" not in m.last_request.qs


def test_session_never_retries_connect_or_read_failures():
    retries = SSIApi._build_session().get_adapter("https://").max_retries
    assert retries.read == 0
    assert retries.connect == 0
    assert retries.total == 3


@pytest.mark.parametrize(
    "exc",
    [
        requests.exceptions.ConnectTimeout,
        requests.exceptions.ReadTimeout,
        requests.exceptions.ConnectionError,
    ],
)
def test_silence_is_reported_as_timeout(exc):
    """Rate limiting shows up as any of these; all must be told apart from
    real API errors so the caller can back off."""
    with mock.patch(
        "divessi.api.SSIApi._authenticate", return_value="token"
    ), requests_mock.Mocker() as m, pytest.raises(TimeoutException):
        m.get(BASE_URL, exc=exc)
        SSIApi("email", "password").get_raw("get_divelog", timeout=1)


def test_authenticate_404_is_authentication_error():
    """The live server answers 404 with [] when credentials are blank."""
    with requests_mock.Mocker() as m, pytest.raises(AuthenticationException):
        m.get(BASE_URL, status_code=404, json=[])
        SSIApi("email", "password")


def test_get_raw_404_is_unknown_command():
    with mock.patch(
        "divessi.api.SSIApi._authenticate", return_value="token"
    ), requests_mock.Mocker() as m, pytest.raises(UnknownCommandException):
        m.get(BASE_URL, status_code=404, json=[])
        SSIApi("email", "password").get_raw("get_nope")


def test_get_raw_rejected_token_inside_200():
    """The live server reports a dead token as HTTP 200 with status 401."""
    body = {
        "status": 401,
        "authenticated": False,
        "authenticated_message": "token_not_valid",
        "message": "token_not_valid",
        "result": "Token not valid (token_not_found) ",
    }

    with mock.patch(
        "divessi.api.SSIApi._authenticate", return_value="token"
    ), requests_mock.Mocker() as m, pytest.raises(TokenException):
        m.get(BASE_URL, json=body)
        SSIApi("email", "password").get_raw("get_divelog")


DIVELOG_PAYLOAD = {
    "logbook_sites": [{"odin_dive_sites_id": 1, "odin_dive_sites_name": "Thistlegorm"}],
    "logbook_details": [
        {
            "odin_user_log_nr": 1,
            "odin_user_log_dive_sites_id": 1,
            "odin_user_log_date": "2023-09-06",
            "odin_user_log_entry_time": "10:00",
            "odin_user_log_divetime": 43,
            "odin_user_log_comment": "amazing!",
            "odin_user_log_depth_m": 33,
            "odin_user_log_watertemp_c": 25,
            "odin_user_log_watertemp_max_c": 27,
            "odin_user_log_weight_kg": 8,
            "odin_user_log_var_tanktype_id": 20,
            "odin_user_log_tank_vol_l": 12,
            "odin_user_log_ean": True,
            "odin_user_log_ean_percent": 32,
            "odin_user_log_pressure_start_bar": 200,
            "odin_user_log_pressure_end_bar": 80,
            "odin_user_log_avg_depth_m": 16.9,
            "odin_user_log_amv_l": 13,
            "odin_user_log_gear_details": "5mm",
            "odin_user_log_divecenter_confirmed_name": "SSI Dive Center",
        }
    ],
}


def _api_with(payload):
    with mock.patch(
        "divessi.api.SSIApi._authenticate", return_value="token"
    ), requests_mock.Mocker() as m:
        m.get(BASE_URL, json=payload)
        return SSIApi("email", "password").get_divelog()


def test_get_divelog():
    divelog = _api_with(DIVELOG_PAYLOAD)

    assert len(divelog) == 1
    dive = divelog[0]
    assert dive["number"] == 1
    assert dive["site"] == "Thistlegorm"
    assert dive["date"] == "2023-09-06"
    assert dive["tank_type"] == "Aluminium"
    assert dive["gas"] == "Nitrox 32%"
    assert dive["divecenter"] == "SSI Dive Center"


def test_get_divelog_unknown_tank_type_does_not_crash():
    payload = {
        "logbook_sites": DIVELOG_PAYLOAD["logbook_sites"],
        "logbook_details": [
            dict(
                DIVELOG_PAYLOAD["logbook_details"][0], odin_user_log_var_tanktype_id=99
            )
        ],
    }

    assert _api_with(payload)[0]["tank_type"] == ""


def test_get_divelog_unknown_site_does_not_crash():
    payload = {
        "logbook_sites": [],
        "logbook_details": DIVELOG_PAYLOAD["logbook_details"],
    }

    assert _api_with(payload)[0]["site"] == ""


def test_get_divelog_missing_fields_do_not_crash():
    payload = {
        "logbook_sites": [],
        "logbook_details": [{"odin_user_log_nr": 7}],
    }

    dive = _api_with(payload)[0]
    assert dive["number"] == 7
    assert dive["gas"] == "Air"
    assert dive["depth"] is None


def test_get_divelog_empty_logbook():
    assert _api_with({"logbook_sites": [], "logbook_details": []}) == []
