import json
import sys
from unittest import mock

import requests_mock

import export
from divessi.constants import BASE_URL


def test_harvest_urls_finds_media_only():
    payload = {
        "a": "https://media.divessi.com/x/photo.JPG",
        "b": {"c": ["https://divessi.com/card.pdf", "https://divessi.com/page.html"]},
        "d": "see https://cdn.divessi.com/clip.mp4, thanks",
    }

    assert export.harvest_urls(payload) == [
        "https://cdn.divessi.com/clip.mp4",
        "https://divessi.com/card.pdf",
        "https://media.divessi.com/x/photo.JPG",
    ]


def test_safe_name():
    assert export.safe_name("get divelog/../x") == "get_divelog_.._x"
    assert export.safe_name("///") == "unnamed"


def test_credentials_file_two_lines(tmp_path):
    path = tmp_path / "config"
    path.write_text("me@example.com\ns3cr&t pw\n", encoding="utf-8")

    assert export.credentials(str(path)) == ("me@example.com", "s3cr&t pw")


def test_credentials_file_ignores_blanks_comments_and_bom(tmp_path):
    path = tmp_path / "config"
    path.write_bytes(b"\xef\xbb\xbf# ssi login\r\n\r\n  me@example.com \r\npw\r\n")

    assert export.credentials(str(path)) == ("me@example.com", "pw")


def test_credentials_file_too_short_exits(tmp_path):
    path = tmp_path / "config"
    path.write_text("me@example.com\n", encoding="utf-8")

    with mock.patch.object(sys, "exit", side_effect=SystemExit) as exit_mock:
        try:
            export.credentials(str(path))
        except SystemExit:
            pass

    assert "two lines" in exit_mock.call_args[0][0]


def test_credentials_file_wins_over_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SSI_USERNAME", "env@example.com")
    monkeypatch.setenv("SSI_PASSWORD", "envpw")
    path = tmp_path / "config"
    path.write_text("file@example.com\nfilepw\n", encoding="utf-8")

    assert export.credentials(str(path)) == ("file@example.com", "filepw")
    assert export.credentials() == ("env@example.com", "envpw")


def test_dump_payload_writes_json_and_csv(tmp_path):
    payload = {"logbook_sites": [{"id": 1}], "logbook_details": [{"nr": 1}], "n": 1}

    info = export.dump_payload("get_divelog", payload, tmp_path)

    assert (
        json.loads((tmp_path / "raw" / "get_divelog.json").read_text("utf-8"))
        == payload
    )
    assert set(info["tables"]) == {
        "get_divelog__logbook_sites.csv",
        "get_divelog__logbook_details.csv",
        "get_divelog__summary.csv",
    }


def test_download_media_skips_foreign_hosts_by_default(tmp_path):
    import requests

    session = requests.Session()
    with requests_mock.Mocker() as m:
        m.get("https://media.divessi.com/a.jpg", content=b"jpg")
        m.get("https://evil.example/b.jpg", content=b"no")
        stats = export.download_media(
            ["https://media.divessi.com/a.jpg", "https://evil.example/b.jpg"],
            tmp_path,
            session,
            delay=0,
        )

    assert stats == {"found": 2, "saved": 1, "skipped": 1, "failed": 0}
    assert len(list(tmp_path.iterdir())) == 1


def test_main_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SSI_USERNAME", "email")
    monkeypatch.setenv("SSI_PASSWORD", "pw")

    divelog = {
        "logbook_sites": [{"odin_dive_sites_id": 1, "odin_dive_sites_name": "Reef"}],
        "logbook_details": [
            {
                "odin_user_log_nr": 1,
                "odin_user_log_dive_sites_id": 1,
                "odin_user_log_date": "2024-01-01",
                "odin_user_log_ean": False,
                "photo": "https://media.divessi.com/d1.jpg",
            }
        ],
    }

    def responder(request, context):
        what = request.qs.get("what", [""])[0]
        if what == "authenticate":
            return {"token": "t"}
        if what == "get_divelog":
            return divelog
        if what == "get_profile":
            return {"name": "Diver"}
        context.status_code = 404
        return []

    argv = [
        "export.py",
        "--out",
        str(tmp_path),
        "--only",
        "get_divelog,get_profile,get_nothing",
        "--delay",
        "0",
        "--subsurface",
    ]

    with requests_mock.Mocker() as m, mock.patch.object(sys, "argv", argv):
        m.get(BASE_URL, json=responder)
        m.get("https://media.divessi.com/d1.jpg", content=b"jpg")
        assert export.main() == 0

    # the curated divelog.csv must come from the payload already fetched,
    # not from a second get_divelog call
    divelog_calls = [
        r for r in m.request_history if r.qs.get("what") == ["get_divelog"]
    ]
    assert len(divelog_calls) == 1

    manifest = json.loads((tmp_path / "manifest.json").read_text("utf-8"))
    assert manifest["commands_with_data"] == ["get_divelog", "get_profile"]
    assert manifest["commands_unknown"] == ["get_nothing"]
    assert manifest["commands_skipped"] == []
    assert manifest["files"]["divelog.csv"]["rows"] == 1
    assert manifest["files"]["subsurface.ssrf"]["dives"] == 1
    assert manifest["media"]["saved"] == 1
    assert (tmp_path / "raw" / "get_profile.json").exists()
    assert (tmp_path / "csv" / "get_divelog__logbook_details.csv").exists()
    assert (tmp_path / "subsurface.ssrf").exists()

    out = capsys.readouterr().out
    assert "OK" in out and "get_nothing" in out
