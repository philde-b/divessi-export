import csv
from typing import Any, Dict, Iterable, List, Tuple

SEPARATOR = "."
LIST_JOIN = "; "


def flatten(obj: Any, prefix: str = "") -> Dict[str, Any]:
    """Flatten nested JSON into a single dict of scalar values.

    Nested keys are joined with dots. Lists of scalars are joined into one
    cell; lists of objects are expanded with an index so nothing is lost.
    """
    flat: Dict[str, Any] = {}

    if isinstance(obj, dict):
        for key, value in obj.items():
            child = f"{prefix}{SEPARATOR}{key}" if prefix else str(key)
            flat.update(flatten(value, child))
    elif isinstance(obj, list):
        if obj and all(not isinstance(item, (dict, list)) for item in obj):
            flat[prefix] = LIST_JOIN.join("" if i is None else str(i) for i in obj)
        else:
            for index, item in enumerate(obj):
                flat.update(flatten(item, f"{prefix}[{index}]"))
    else:
        flat[prefix] = obj

    return flat


def tables(payload: Any) -> List[Tuple[str, List[Dict[str, Any]]]]:
    """Split an API payload into named tables ready for CSV.

    A dict whose values are lists of objects becomes one table per list,
    which is how get_divelog is shaped (logbook_sites, logbook_details).
    Anything else becomes a single table.
    """
    if isinstance(payload, list):
        return [("rows", [flatten(item) for item in payload])]

    if not isinstance(payload, dict):
        return [("rows", [{"value": payload}])]

    list_keys = [
        key
        for key, value in payload.items()
        if isinstance(value, list) and value and isinstance(value[0], dict)
    ]

    if not list_keys:
        return [("rows", [flatten(payload)])]

    result = [(key, [flatten(item) for item in payload[key]]) for key in list_keys]

    scalars = {key: value for key, value in payload.items() if key not in list_keys}
    if scalars:
        result.append(("summary", [flatten(scalars)]))

    return result


def fieldnames(rows: Iterable[Dict[str, Any]]) -> List[str]:
    """Union of every key across rows, in first-seen order."""
    names: List[str] = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                names.append(key)
    return names


def write_csv(rows: List[Dict[str, Any]], path) -> int:
    """Write rows to path and return the number of data rows written."""
    names = fieldnames(rows)

    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=names, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    return len(rows)
