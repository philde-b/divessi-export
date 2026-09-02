#!/usr/bin/env python3
"""Convert a raw divessi-export get_divelog.json backup to Subsurface XML.

The converter deliberately does not invent a dive profile.  If MySSI supplies
summary values only, Subsurface receives the real duration, maximum/mean depth,
temperatures and other logbook metadata without synthetic depth samples.

Usage:
    python -m divessi.subsurface get_divelog.json myssi-import.ssrf

Only the Python standard library is required.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable

TANK_TYPES = {19: "Steel", 20: "Aluminium"}


def is_present(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def nonzero(value: Any) -> bool:
    if not is_present(value) or value is False:
        return False
    try:
        return float(value) != 0
    except (TypeError, ValueError):
        return True


def as_float(value: Any) -> float | None:
    if not nonzero(value):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def fmt_number(value: float, places: int = 3) -> str:
    return f"{value:.{places}f}".rstrip("0").rstrip(".")


def xml_text(value: Any) -> str:
    """Return XML 1.0-safe text, removing only forbidden control characters."""
    text = str(value)
    return "".join(
        ch
        for ch in text
        if ch in "\t\n\r"
        or "\x20" <= ch <= "\ud7ff"
        or "\ue000" <= ch <= "\ufffd"
        or "\U00010000" <= ch <= "\U0010ffff"
    )


def extra_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return xml_text(value)


def add_text(parent: ET.Element, tag: str, value: Any) -> ET.Element | None:
    if not is_present(value):
        return None
    node = ET.SubElement(parent, tag)
    node.text = xml_text(value)
    return node


def first_present(*values: Any) -> Any:
    return next((value for value in values if is_present(value)), None)


def celsius(row: dict[str, Any], c_key: str, f_key: str) -> float | None:
    c_value = as_float(row.get(c_key))
    if c_value is not None:
        return c_value
    f_value = as_float(row.get(f_key))
    if f_value is not None:
        return (f_value - 32.0) * 5.0 / 9.0
    return None


def normalized_time(value: Any) -> str:
    text = str(value or "00:00:00").strip()
    if re.fullmatch(r"\d{1,2}:\d{2}", text):
        return f"{text}:00"
    if re.fullmatch(r"\d{1,2}:\d{2}:\d{2}", text):
        return text
    return "00:00:00"


def duration_text(value: Any) -> str | None:
    minutes = as_float(value)
    if minutes is None:
        return None
    seconds = max(0, round(minutes * 60))
    return f"{seconds // 60}:{seconds % 60:02d} min"


def site_uuid(site_id: Any, name: Any, used: set[str]) -> str:
    seed = f"myssi-site:{site_id}:{name}".encode("utf-8", "replace")
    counter = 0
    while True:
        material = seed if counter == 0 else seed + f":{counter}".encode()
        candidate = hashlib.sha256(material).hexdigest()[:8]
        if candidate != "00000000" and candidate not in used:
            used.add(candidate)
            return candidate
        counter += 1


def display_name(person: dict[str, Any]) -> str:
    first = first_present(person.get("firstname"), person.get("forename"))
    last = person.get("lastname")
    full = " ".join(str(part).strip() for part in (first, last) if is_present(part))
    if full:
        return full
    return str(first_present(person.get("nickname"), person.get("email"), "")).strip()


def make_buddy_map(payload: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for person in payload.get("logbook_buddies") or []:
        if not isinstance(person, dict):
            continue
        name = display_name(person)
        if not name:
            continue
        for key in ("id", "buddy_master_id", "master_id"):
            if is_present(person.get(key)):
                result[str(person[key])] = name
    return result


def valid_gps(lat_value: Any, lon_value: Any) -> tuple[float, float] | None:
    try:
        lat, lon = float(lat_value), float(lon_value)
    except (TypeError, ValueError):
        return None
    if (
        math.isfinite(lat)
        and math.isfinite(lon)
        and -90 <= lat <= 90
        and -180 <= lon <= 180
    ):
        return lat, lon
    return None


def unique_text(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, list):
            candidates = value
        else:
            candidates = [value]
        for candidate in candidates:
            if not is_present(candidate):
                continue
            text = str(candidate).strip()
            if text and text not in seen:
                seen.add(text)
                result.append(text)
    return result


def build_sites(
    payload: dict[str, Any], parent: ET.Element
) -> tuple[dict[str, str], int]:
    id_to_uuid: dict[str, str] = {}
    used_uuids: set[str] = set()
    written = 0

    for site in payload.get("logbook_sites") or []:
        if not isinstance(site, dict):
            continue
        site_id = site.get("odin_dive_sites_id")
        name = first_present(site.get("odin_dive_sites_name"), f"MySSI site {site_id}")
        uuid = site_uuid(site_id, name, used_uuids)
        if is_present(site_id):
            id_to_uuid[str(site_id)] = uuid

        attrs = {"uuid": uuid, "name": xml_text(name)}
        gps = valid_gps(
            site.get("odin_dive_sites_lat"), site.get("odin_dive_sites_lon")
        )
        if gps:
            attrs["gps"] = f"{fmt_number(gps[0], 6)} {fmt_number(gps[1], 6)}"

        description = site.get("odin_dive_sites_comment")
        if is_present(description):
            attrs["description"] = xml_text(description)

        node = ET.SubElement(parent, "site", attrs)
        note_lines: list[str] = []
        if is_present(site_id):
            note_lines.append(f"SSI site ID: {site_id}")

        location_bits = unique_text(
            [
                site.get("odin_dive_sites_meta_address"),
                site.get("odin_dive_sites_address"),
                site.get("odin_dive_sites_meta_area"),
                site.get("odin_dive_sites_areas_name"),
                site.get("odin_dive_sites_meta_region"),
                site.get("odin_dive_sites_regions_name"),
                site.get("odin_dive_sites_meta_country"),
                site.get("odin_countries"),
                site.get("odin_dive_sites_country_iso3"),
            ]
        )
        if location_bits:
            note_lines.append("SSI location: " + ", ".join(location_bits))

        aliases = unique_text([site.get("alias_names")])
        if aliases:
            note_lines.append("SSI aliases: " + ", ".join(aliases))
        if is_present(site.get("odin_dive_sites_pos_verified")):
            note_lines.append(
                "SSI position verified: "
                + extra_value(site["odin_dive_sites_pos_verified"])
            )
        if is_present(site.get("odin_dive_sites_is_private")):
            note_lines.append(
                "SSI private site: " + extra_value(site["odin_dive_sites_is_private"])
            )

        if note_lines:
            add_text(node, "notes", "\n".join(note_lines))
        written += 1

    return id_to_uuid, written


def meaningful_notes(row: dict[str, Any], unresolved_buddies: list[str]) -> str:
    sections: list[str] = []
    comment = row.get("odin_user_log_comment")
    if is_present(comment):
        sections.append(str(comment).strip())

    details: list[str] = []
    centre = row.get("odin_user_log_divecenter_confirmed_name")
    if is_present(centre):
        details.append(f"Dive centre: {centre}")
    if nonzero(row.get("odin_user_log_vis_m")):
        details.append(f"Visibility: {fmt_number(float(row['odin_user_log_vis_m']))} m")
    if nonzero(row.get("odin_user_log_amv_l")):
        amv = fmt_number(float(row["odin_user_log_amv_l"]))
        details.append(f"SSI AMV/SAC: {amv} l/min")
    if is_present(row.get("odin_user_log_gear_details")):
        details.append(f"Gear details: {row['odin_user_log_gear_details']}")

    water_min = celsius(row, "odin_user_log_watertemp_c", "odin_user_log_watertemp_f")
    water_max = celsius(
        row, "odin_user_log_watertemp_max_c", "odin_user_log_watertemp_max_f"
    )
    if (
        water_min is not None
        and water_max is not None
        and abs(water_min - water_max) >= 0.05
    ):
        temperature_range = (
            f"{fmt_number(water_min, 1)}–{fmt_number(water_max, 1)} °C"
        )
        details.append(
            f"SSI water temperature range: {temperature_range}"
        )

    code_fields = (
        ("Weather code", "odin_user_log_var_weather_id"),
        ("Current code", "odin_user_log_var_current_id"),
        ("Entry code", "odin_user_log_var_entry_id"),
        ("Surface code", "odin_user_log_var_surface_id"),
        ("Water-body code", "odin_user_log_var_water_body_id"),
        ("Water-type code", "odin_user_log_var_watertype_id"),
        ("Special-dive code(s)", "odin_user_log_var_specialdive_id"),
        ("Dive-type code", "odin_user_log_var_divetype_id"),
    )
    for label, key in code_fields:
        if nonzero(row.get(key)):
            details.append(f"{label} (SSI): {row[key]}")

    animal_ids = row.get("odin_user_log_animal_ids") or []
    if animal_ids:
        details.append("Wildlife IDs (SSI): " + ", ".join(map(str, animal_ids)))
    if unresolved_buddies:
        details.append("Unresolved buddy IDs (SSI): " + ", ".join(unresolved_buddies))

    if details:
        sections.append("[MySSI details]\n" + "\n".join(details))
    return "\n\n".join(sections)


def add_cylinder(dive: ET.Element, row: dict[str, Any]) -> bool:
    keys = (
        "odin_user_log_tank_vol_l",
        "odin_user_log_pressure_start_bar",
        "odin_user_log_pressure_end_bar",
        "odin_user_log_ean",
        "odin_user_log_var_tanktype_id",
    )
    if not any(nonzero(row.get(key)) for key in keys):
        return False

    attrs: dict[str, str] = {}
    size = as_float(row.get("odin_user_log_tank_vol_l"))
    if size is not None:
        attrs["size"] = f"{fmt_number(size)} l"

    tank_id = row.get("odin_user_log_var_tanktype_id")
    try:
        description = TANK_TYPES.get(int(tank_id))
    except (TypeError, ValueError):
        description = None
    attrs["description"] = description or "SSI cylinder"

    if row.get("odin_user_log_ean") and nonzero(row.get("odin_user_log_ean_percent")):
        attrs["o2"] = f"{fmt_number(float(row['odin_user_log_ean_percent']), 1)}%"

    start = as_float(row.get("odin_user_log_pressure_start_bar"))
    end = as_float(row.get("odin_user_log_pressure_end_bar"))
    if start is not None:
        attrs["start"] = f"{fmt_number(start)} bar"
    if end is not None:
        attrs["end"] = f"{fmt_number(end)} bar"
    ET.SubElement(dive, "cylinder", attrs)
    return True


def add_extra_data(dc: ET.Element, row: dict[str, Any]) -> int:
    count = 0
    for key in sorted(row):
        value = row[key]
        if not is_present(value):
            continue
        rendered = extra_value(value)
        if not rendered:
            continue
        ET.SubElement(
            dc,
            "extradata",
            {"key": xml_text(f"SSI: {key}"), "value": rendered},
        )
        count += 1
    return count


def add_dive(
    parent: ET.Element,
    row: dict[str, Any],
    site_ids: dict[str, str],
    buddy_map: dict[str, str],
) -> tuple[bool, int, int, bool]:
    date = row.get("odin_user_log_date")
    if not is_present(date):
        return False, 0, 0, False

    attrs: dict[str, str] = {
        "date": xml_text(date),
        "time": normalized_time(row.get("odin_user_log_entry_time")),
    }
    if nonzero(row.get("odin_user_log_nr")):
        attrs["number"] = str(int(float(row["odin_user_log_nr"])))
    rating = as_float(row.get("odin_user_log_rating"))
    if rating is not None and 0 <= rating <= 5:
        attrs["rating"] = str(round(rating))
    duration = duration_text(row.get("odin_user_log_divetime"))
    if duration:
        attrs["duration"] = duration

    raw_site_id = row.get("odin_user_log_dive_sites_id")
    if is_present(raw_site_id) and str(raw_site_id) in site_ids:
        attrs["divesiteid"] = site_ids[str(raw_site_id)]

    tags = ["SSI import"]
    if row.get("odin_user_log_verified"):
        tags.append("SSI verified")
    if row.get("odin_user_log_ean"):
        tags.append("Nitrox")
    apple_watch = nonzero(row.get("odin_user_log_apple_watch_log_id"))
    if apple_watch:
        tags.append("Apple Watch")
    attrs["tags"] = ", ".join(tags)

    dive = ET.SubElement(parent, "dive", attrs)

    leader = first_present(
        row.get("odin_user_log_leader_confirmed_name"),
        row.get("odin_user_log_user_confirmed_name"),
    )
    add_text(dive, "divemaster", leader)

    buddy_names: list[str] = []
    unresolved: list[str] = []
    for buddy_id in row.get("odin_user_log_buddy_ids") or []:
        name = buddy_map.get(str(buddy_id))
        if name and name not in buddy_names:
            buddy_names.append(name)
        elif not name:
            unresolved.append(str(buddy_id))
    confirmed_buddy = row.get("odin_user_log_buddy_confirmed_name")
    if is_present(confirmed_buddy) and str(confirmed_buddy) not in buddy_names:
        buddy_names.append(str(confirmed_buddy))
    add_text(dive, "buddy", ", ".join(buddy_names))

    notes = meaningful_notes(row, unresolved)
    add_text(dive, "notes", notes)

    cylinder_added = add_cylinder(dive, row)
    weight = as_float(row.get("odin_user_log_weight_kg"))
    if weight is not None:
        ET.SubElement(
            dive,
            "weightsystem",
            {"weight": f"{fmt_number(weight)} kg", "description": "SSI logged weight"},
        )

    dc_model = "Apple Watch via MySSI" if apple_watch else "MySSI import"
    dc = ET.SubElement(dive, "divecomputer", {"model": dc_model})
    max_depth = as_float(row.get("odin_user_log_depth_m"))
    mean_depth = as_float(row.get("odin_user_log_avg_depth_m"))
    if max_depth is not None or mean_depth is not None:
        depth_attrs: dict[str, str] = {}
        if max_depth is not None:
            depth_attrs["max"] = f"{fmt_number(max_depth)} m"
        if mean_depth is not None:
            depth_attrs["mean"] = f"{fmt_number(mean_depth)} m"
        ET.SubElement(dc, "depth", depth_attrs)

    air_temp = celsius(row, "odin_user_log_airtemp_c", "odin_user_log_airtemp_f")
    water_temp = celsius(row, "odin_user_log_watertemp_c", "odin_user_log_watertemp_f")
    if water_temp is None:
        water_temp = celsius(
            row, "odin_user_log_watertemp_max_c", "odin_user_log_watertemp_max_f"
        )
    if air_temp is not None or water_temp is not None:
        temp_attrs: dict[str, str] = {}
        if air_temp is not None:
            temp_attrs["air"] = f"{fmt_number(air_temp, 1)} C"
        if water_temp is not None:
            temp_attrs["water"] = f"{fmt_number(water_temp, 1)} C"
        ET.SubElement(dc, "temperature", temp_attrs)

    extra_count = add_extra_data(dc, row)
    return True, len(buddy_names), extra_count, cylinder_added


def convert(source: Path, destination: Path) -> dict[str, int]:
    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read {source}: {exc}") from exc

    if not isinstance(payload, dict) or not isinstance(
        payload.get("logbook_details"), list
    ):
        raise ValueError("Input is not a raw divessi-export get_divelog.json file")

    root = ET.Element("divelog", {"program": "subsurface", "version": "3"})
    ET.SubElement(root, "settings")
    sites_node = ET.SubElement(root, "divesites")
    site_ids, site_count = build_sites(payload, sites_node)
    dives_node = ET.SubElement(root, "dives")
    buddy_map = make_buddy_map(payload)

    rows = [row for row in payload["logbook_details"] if isinstance(row, dict)]
    rows.sort(
        key=lambda row: (
            str(row.get("odin_user_log_date") or ""),
            normalized_time(row.get("odin_user_log_entry_time")),
            float(row.get("odin_user_log_nr") or 0),
        )
    )

    stats = {
        "dives": 0,
        "skipped": 0,
        "sites": site_count,
        "buddy_links": 0,
        "extra_fields": 0,
        "cylinders": 0,
        "profile_samples": 0,
    }
    for row in rows:
        added, buddies, extras, cylinder = add_dive(
            dives_node, row, site_ids, buddy_map
        )
        if not added:
            stats["skipped"] += 1
            continue
        stats["dives"] += 1
        stats["buddy_links"] += buddies
        stats["extra_fields"] += extras
        stats["cylinders"] += int(cylinder)

    ET.indent(root, space="  ")
    tree = ET.ElementTree(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    tree.write(
        destination,
        encoding="utf-8",
        xml_declaration=True,
        short_empty_elements=True,
    )
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert raw MySSI/divessi-export JSON to native Subsurface XML."
    )
    parser.add_argument("input", type=Path, help="raw/get_divelog.json")
    parser.add_argument(
        "output",
        nargs="?",
        type=Path,
        help="destination .ssrf file (default: INPUT.with_suffix('.ssrf'))",
    )
    args = parser.parse_args()
    output = args.output or args.input.with_name("myssi-subsurface-import.ssrf")

    try:
        stats = convert(args.input, output)
    except ValueError as exc:
        parser.error(str(exc))

    print(f"Wrote: {output.resolve()}")
    print(f"Dives: {stats['dives']} (skipped: {stats['skipped']})")
    print(f"Dive sites: {stats['sites']}")
    print(f"Resolved buddy links: {stats['buddy_links']}")
    print(f"Cylinders: {stats['cylinders']}")
    print(f"Preserved SSI extra fields: {stats['extra_fields']}")
    if stats["profile_samples"] == 0:
        print("Depth-profile samples: 0 (none were supplied by this MySSI export)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
