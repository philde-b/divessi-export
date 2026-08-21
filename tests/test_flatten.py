import csv

from divessi.flatten import fieldnames, flatten, tables, write_csv


def test_flatten_scalars_and_nesting():
    assert flatten({"a": 1, "b": {"c": 2, "d": {"e": 3}}}) == {
        "a": 1,
        "b.c": 2,
        "b.d.e": 3,
    }


def test_flatten_scalar_list_is_joined():
    assert flatten({"tags": ["x", "y", None]}) == {"tags": "x; y; "}


def test_flatten_object_list_is_indexed():
    assert flatten({"items": [{"n": 1}, {"n": 2}]}) == {
        "items[0].n": 1,
        "items[1].n": 2,
    }


def test_flatten_empty_containers():
    assert flatten({"a": [], "b": {}}) == {}


def test_tables_splits_lists_of_objects():
    payload = {
        "logbook_sites": [{"id": 1}],
        "logbook_details": [{"nr": 1}, {"nr": 2}],
        "total": 2,
    }

    result = dict(tables(payload))

    assert set(result) == {"logbook_sites", "logbook_details", "summary"}
    assert len(result["logbook_details"]) == 2
    assert result["summary"] == [{"total": 2}]


def test_tables_plain_object_becomes_single_row():
    assert tables({"name": "x", "level": 3}) == [("rows", [{"name": "x", "level": 3}])]


def test_tables_top_level_list():
    assert tables([{"a": 1}, {"a": 2}]) == [("rows", [{"a": 1}, {"a": 2}])]


def test_tables_scalar():
    assert tables("ok") == [("rows", [{"value": "ok"}])]


def test_fieldnames_union_preserves_first_seen_order():
    rows = [{"a": 1, "b": 2}, {"c": 3, "a": 4}]
    assert fieldnames(rows) == ["a", "b", "c"]


def test_write_csv_handles_ragged_rows(tmp_path):
    rows = [{"a": 1, "b": "x"}, {"a": 2, "c": "ü"}]
    path = tmp_path / "out.csv"

    assert write_csv(rows, path) == 2

    with open(path, encoding="utf-8-sig", newline="") as handle:
        read = list(csv.DictReader(handle))

    assert read == [
        {"a": "1", "b": "x", "c": ""},
        {"a": "2", "b": "", "c": "ü"},
    ]
