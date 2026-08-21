import logging
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .constants import BASE_URL, CLIENT_APP, REQUEST_TIMEOUT, TANK_TYPES
from .exceptions import (
    ApiException,
    AuthenticationException,
    TimeoutException,
    TokenException,
    UnknownCommandException,
)

logger = logging.getLogger(__name__)


class SSIApi:
    """Client for the endpoint the MySSI app talks to.

    The service is a single PHP dispatcher: every call is a GET against the
    same URL with a "what" parameter naming the command. Credentials are
    passed as query parameters, which is the server's design and not
    something this client can improve on.
    """

    def __init__(
        self,
        username: str,
        password: str,
        base_url: str = BASE_URL,
        client_app: str = CLIENT_APP,
        timeout: int = REQUEST_TIMEOUT,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.base_url = base_url
        self.client_app = client_app
        self.timeout = timeout
        self.session = session if session is not None else self._build_session()
        self.token = self._authenticate(username, password)

    @staticmethod
    def _build_session() -> requests.Session:
        session = requests.Session()
        # Retry 429/5xx with backoff, but never a connect or read failure:
        # when the server rate limits it simply stops answering, and every
        # retry would multiply the cost of finding that out.
        retry = Retry(
            total=3,
            connect=0,
            read=0,
            status=3,
            backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET"]),
        )
        session.mount("https://", HTTPAdapter(max_retries=retry))
        session.mount("http://", HTTPAdapter(max_retries=retry))
        return session

    def _get(self, params: Dict[str, Any], timeout: Optional[float] = None) -> Any:
        """Perform one API call and return the decoded JSON body.

        Parameters go through requests' encoder rather than string
        interpolation, so passwords containing &, +, # or spaces survive
        the trip intact.
        """
        payload = dict(params)
        payload["ssiapp"] = self.client_app

        effective_timeout = self.timeout if timeout is None else timeout

        try:
            response = self.session.get(
                self.base_url, params=payload, timeout=effective_timeout
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            # Both look the same from here: the server is not answering,
            # which is how it rate limits.
            raise TimeoutException(
                f"{params.get('what')} got no answer within {effective_timeout}s "
                f"({exc.__class__.__name__})"
            ) from exc
        except requests.RequestException as exc:
            raise ApiException(f"request to {self.base_url} failed: {exc}") from exc

        # The dispatcher answers 404 to any "what" it does not recognise.
        if response.status_code == 404:
            raise UnknownCommandException(
                f"{params.get('what')} is not a known command (HTTP 404)"
            )

        if response.status_code != 200:
            raise ApiException(
                f"{params.get('what')} returned HTTP {response.status_code}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            snippet = response.text[:200].replace("\n", " ")
            raise ApiException(
                f"{params.get('what')} returned non-JSON body: {snippet}"
            ) from exc

        if self._is_auth_failure(body):
            raise TokenException(
                f"{params.get('what')} rejected the token: "
                f"{body.get('message') or body.get('result') or body}"
            )

        return body

    @staticmethod
    def _is_auth_failure(body: Any) -> bool:
        """Auth failures arrive as HTTP 200 with a 401 inside the body."""
        if not isinstance(body, dict):
            return False
        return body.get("authenticated") is False or str(body.get("status")) == "401"

    def _authenticate(self, username: str, password: str) -> str:
        try:
            payload = self._get({"l": username, "p": password, "what": "authenticate"})
        except (TokenException, UnknownCommandException) as exc:
            raise AuthenticationException(str(exc)) from exc

        if not isinstance(payload, dict) or "token" not in payload:
            raise AuthenticationException

        return payload["token"]

    def get_raw(self, what: str, timeout: Optional[float] = None, **extra: Any) -> Any:
        """Return the untouched JSON body for any command.

        This is the call that makes a real backup possible: it keeps every
        field the server sends instead of a hand-picked subset. Any extra
        keyword arguments become query parameters.
        """
        params: Dict[str, Any] = {"what": what, "token": self.token}
        params.update(extra)
        return self._get(params, timeout=timeout)

    def get_divelog_raw(self) -> Any:
        return self.get_raw("get_divelog")

    def get_divelog(self) -> List[Dict[str, Any]]:
        """Return the curated view of the logbook.

        Kept for compatibility with the original script, but no longer
        raises on unmapped tank types, unknown sites or an empty logbook.
        """
        return curate_divelog(self.get_divelog_raw())


def curate_divelog(payload: Any) -> List[Dict[str, Any]]:
    """Reduce a raw get_divelog payload to the classic flat dive list.

    Pure function over an already fetched payload, so callers that hold
    the raw JSON need not spend a second API call on it.
    """
    if not isinstance(payload, dict):
        raise ApiException("get_divelog did not return an object")

    sites_map = {
        site.get("odin_dive_sites_id"): site.get("odin_dive_sites_name", "")
        for site in payload.get("logbook_sites") or []
    }

    logbook = []
    for divelog in payload.get("logbook_details") or []:
        nitrox_percent = divelog.get("odin_user_log_ean_percent")
        tank_type_id = divelog.get("odin_user_log_var_tanktype_id")

        logbook.append(
            dict(
                number=divelog.get("odin_user_log_nr"),
                site=sites_map.get(divelog.get("odin_user_log_dive_sites_id"), ""),
                date=divelog.get("odin_user_log_date"),
                time=divelog.get("odin_user_log_entry_time"),
                divetime=divelog.get("odin_user_log_divetime"),
                comments=divelog.get("odin_user_log_comment"),
                depth=divelog.get("odin_user_log_depth_m"),
                water_temp=divelog.get("odin_user_log_watertemp_c"),
                water_temp_max=divelog.get("odin_user_log_watertemp_max_c"),
                weight=divelog.get("odin_user_log_weight_kg"),
                tank_type=TANK_TYPES.get(tank_type_id, "") if tank_type_id else "",
                tank_size=divelog.get("odin_user_log_tank_vol_l"),
                gas=(
                    f"Nitrox {nitrox_percent}%"
                    if divelog.get("odin_user_log_ean")
                    else "Air"
                ),
                start_bar=divelog.get("odin_user_log_pressure_start_bar"),
                end_bar=divelog.get("odin_user_log_pressure_end_bar"),
                avg_depth=divelog.get("odin_user_log_avg_depth_m"),
                consumption_l=divelog.get("odin_user_log_amv_l"),
                gear_details=divelog.get("odin_user_log_gear_details"),
                divecenter=divelog.get("odin_user_log_divecenter_confirmed_name"),
            )
        )

    return logbook
