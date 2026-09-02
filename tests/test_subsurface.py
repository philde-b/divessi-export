import json
import xml.etree.ElementTree as ET

import pytest

from divessi.subsurface import convert


def sample_payload():
    return {
        "logbook_sites": [
            {
                "odin_dive_sites_id": 7,
                "odin_dive_sites_name": "Example Reef",
                "odin_dive_sites_lat": "57.123456",
                "odin_dive_sites_lon": "-4.654321",
                "odin_dive_sites_meta_country": "United Kingdom",
            }
        ],
        "logbook_buddies": [
            {"id": 9, "firstname": "Example", "lastname": "Buddy"}
        ],
        "logbook_details": [
            {
                "odin_user_log_nr": 42,
                "odin_user_log_date": "2026-08-01",
                "odin_user_log_entry_time": "09:15",
                "odin_user_log_divetime": 47,
                "odin_user_log_depth_m": 24.6,
                "odin_user_log_avg_depth_m": 13.2,
                "odin_user_log_dive_sites_id": 7,
                "odin_user_log_buddy_ids": [9],
                "odin_user_log_leader_confirmed_name": "Example Leader",
                "odin_user_log_comment": "An anonymised test dive.",
                "odin_user_log_rating": 5,
                "odin_user_log_watertemp_c": 11,
                "odin_user_log_airtemp_c": 16,
                "odin_user_log_tank_vol_l": 12,
                "odin_user_log_pressure_start_bar": 210,
                "odin_user_log_pressure_end_bar": 65,
                "odin_user_log_var_tanktype_id": 19,
                "odin_user_log_ean": True,
                "odin_user_log_ean_percent": 32,
                "odin_user_log_weight_kg": 8,
                "odin_user_log_verified": True,
                "unmapped_example": "preserved",
            }
        ],
    }


def test_convert_maps_supported_fields_and_preserves_extras(tmp_path):
    source = tmp_path / "get_divelog.json"
    destination = tmp_path / "subsurface.ssrf"
    source.write_text(json.dumps(sample_payload()), encoding="utf-8")

    stats = convert(source, destination)
    root = ET.parse(destination).getroot()
    site = root.find("./divesites/site")
    dive = root.find("./dives/dive")

    assert stats["dives"] == 1
    assert stats["sites"] == 1
    assert stats["buddy_links"] == 1
    assert stats["cylinders"] == 1
    assert stats["extra_fields"] == len(sample_payload()["logbook_details"][0])

    assert root.attrib == {"program": "subsurface", "version": "3"}
    assert site is not None
    assert site.attrib["name"] == "Example Reef"
    assert site.attrib["gps"] == "57.123456 -4.654321"

    assert dive is not None
    assert dive.attrib["number"] == "42"
    assert dive.attrib["duration"] == "47:00 min"
    assert dive.attrib["divesiteid"] == site.attrib["uuid"]
    assert dive.findtext("buddy") == "Example Buddy"
    assert dive.findtext("divemaster") == "Example Leader"

    cylinder = dive.find("cylinder")
    assert cylinder is not None
    assert cylinder.attrib == {
        "size": "12 l",
        "description": "Steel",
        "o2": "32%",
        "start": "210 bar",
        "end": "65 bar",
    }
    assert dive.find("weightsystem").attrib["weight"] == "8 kg"

    extras = {
        item.attrib["key"]: item.attrib["value"]
        for item in dive.findall("./divecomputer/extradata")
    }
    assert extras["SSI: unmapped_example"] == "preserved"
    assert extras["SSI: odin_user_log_buddy_ids"] == "[9]"


def test_convert_rejects_non_divelog_json(tmp_path):
    source = tmp_path / "wrong.json"
    source.write_text('{"not": "a divelog"}', encoding="utf-8")

    with pytest.raises(ValueError, match="not a raw divessi-export"):
        convert(source, tmp_path / "out.ssrf")


def test_convert_skips_records_without_dates(tmp_path):
    payload = sample_payload()
    payload["logbook_details"].append({"odin_user_log_nr": 43})
    source = tmp_path / "get_divelog.json"
    source.write_text(json.dumps(payload), encoding="utf-8")

    stats = convert(source, tmp_path / "out.ssrf")

    assert stats["dives"] == 1
    assert stats["skipped"] == 1
