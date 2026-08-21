import logging
import time
from typing import Any, Dict, Iterable, Optional

from .constants import DEFAULT_DELAY, DISCOVERY_CANDIDATES, DISCOVERY_COOLDOWN
from .exceptions import (
    ApiException,
    TimeoutException,
    TokenException,
    UnknownCommandException,
)

logger = logging.getLogger(__name__)

ERROR_MARKERS = ("error", "err", "status")

# A guessed command the server sits on must fail fast; a real one answers
# in a couple of seconds even for a large logbook.
PROBE_TIMEOUT = 15


def looks_empty(payload: Any) -> bool:
    if payload is None:
        return True
    if isinstance(payload, (list, dict, str)) and len(payload) == 0:
        return True
    if isinstance(payload, dict):
        # A body that carries nothing but an error marker is not data.
        meaningful = [
            key
            for key, value in payload.items()
            if key.lower() not in ERROR_MARKERS and value not in (None, "", [], {})
        ]
        if not meaningful:
            return True
    return False


def probe(api, what: str, timeout: float = PROBE_TIMEOUT) -> Dict[str, Any]:
    """Call one command and describe what came back.

    A TokenException is deliberately not caught here: once the token is
    dead every further call will fail the same way, so the caller must
    decide whether to stop.
    """
    try:
        payload = api.get_raw(what, timeout=timeout)
    except UnknownCommandException:
        return {
            "what": what,
            "status": "unknown",
            "detail": "not a known command",
            "payload": None,
        }
    except TimeoutException as exc:
        return {"what": what, "status": "timeout", "detail": str(exc), "payload": None}
    except ApiException as exc:
        return {"what": what, "status": "error", "detail": str(exc), "payload": None}

    if looks_empty(payload):
        return {"what": what, "status": "empty", "detail": "", "payload": payload}

    if isinstance(payload, dict):
        detail = "keys: " + ", ".join(list(payload.keys())[:12])
    elif isinstance(payload, list):
        detail = f"list of {len(payload)}"
    else:
        detail = type(payload).__name__

    return {"what": what, "status": "data", "detail": detail, "payload": payload}


def _skip(results, names, reason: str, on_result) -> None:
    for name in names:
        results[name] = {
            "what": name,
            "status": "skipped",
            "detail": reason,
            "payload": None,
        }
        if on_result:
            on_result(results[name])


def discover(
    api,
    candidates: Iterable[str] = DISCOVERY_CANDIDATES,
    delay: float = DEFAULT_DELAY,
    on_result: Optional[Any] = None,
    cooldown: float = DISCOVERY_COOLDOWN,
    max_consecutive_timeouts: int = 2,
) -> Dict[str, Dict[str, Any]]:
    """Probe candidate commands and return what each one answered.

    Calls are sequential with a delay between them. The server rate limits
    bursts by simply not answering any more, so after a streak of timeouts
    the loop backs off once for `cooldown` seconds and retries the streak;
    if the silence persists, the remaining candidates are marked skipped
    instead of burning a full timeout each.
    """
    candidates = tuple(candidates)
    results: Dict[str, Dict[str, Any]] = {}
    index = 0
    streak = 0
    streak_start = 0
    cooled_down = False

    while index < len(candidates):
        what = candidates[index]
        try:
            result = probe(api, what)
        except TokenException as exc:
            # The token died; everything from here on would fail identically.
            logger.warning("token rejected at %s, stopping: %s", what, exc)
            _skip(results, candidates[index:], "token rejected", on_result)
            break

        if result["status"] == "timeout":
            if streak == 0:
                streak_start = index
            streak += 1
            if streak >= max_consecutive_timeouts:
                if cooldown and not cooled_down:
                    logger.warning(
                        "%d timeouts in a row, backing off %ss", streak, cooldown
                    )
                    time.sleep(cooldown)
                    cooled_down = True
                    streak = 0
                    for name in candidates[streak_start:index]:
                        results.pop(name, None)
                    index = streak_start
                    continue
                _skip(results, candidates[streak_start:], "rate limited", on_result)
                break
        else:
            streak = 0

        results[what] = result
        if on_result:
            on_result(result)
        logger.info("%s -> %s %s", what, result["status"], result["detail"])

        index += 1
        if delay and index < len(candidates):
            time.sleep(delay)

    return results
