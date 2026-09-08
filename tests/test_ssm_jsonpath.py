"""Tests del motor de diff JSONPath (BBIT-2)."""

import json

from bbit_release.ssm.jsonpath import (
    WARNING_MSG,
    apply_updates,
    diff_json,
    parse_value,
)


def test_parse_value_parses_json_text():
    assert parse_value('{"a":1}') == {"a": 1}
    assert parse_value('[1]') == [1]


def test_parse_value_keeps_scalar():
    assert parse_value("hola") == "hola"
    assert parse_value("{\"rot") == "{\"rot"  # no es JSON válido


def test_diff_json_identical_no_updates():
    r = diff_json('{"value": {"details": {"max": 1}}}', '{"value": {"details": {"max": 1}}}')
    assert r["updates"] == []
    assert r["warning"] is None


def test_diff_json_leaf_update():
    r = diff_json('{"value": {"details": {"max": 100}}}', '{"value": {"details": {"max": 90}}}')
    assert r["updates"] == [
        {"op": "update", "path": "$.value.details.max", "value": 100, "json": False}
    ]
    assert r["warning"] is None


def test_diff_json_insert_subtree_warns():
    origin = '{"value": {"details": {"max": 100}, "extra": {"x": [1, 2]}}}'
    dest = '{"value": {"details": {"max": 100}}}'
    r = diff_json(origin, dest)
    insert = [u for u in r["updates"] if u["op"] == "insert"]
    assert insert == [
        {
            "op": "insert",
            "path": "$.value.extra",
            "value": '{"x":[1,2]}',
            "json": True,
        }
    ]
    assert r["warning"] == WARNING_MSG


def test_diff_json_extra_data_in_dest_warns():
    r = diff_json('{"a": 1}', '{"a": 1, "residual": true}')
    assert r["updates"] == []
    assert r["warning"] == WARNING_MSG


def test_diff_json_unordered_origin_nested_modify():
    r = diff_json(
        '{"cn": {"limits": {"max": 10, "min": 1}}, "pref": "x"}',
        '{"cn": {"limits": {"max": 5, "min": 1}}, "pref": "x"}',
    )
    assert r["updates"] == [
        {"op": "update", "path": "$.cn.limits.max", "value": 10, "json": False}
    ]
    assert r["warning"] is None


def test_diff_json_deterministic_keys_sorted():
    r = diff_json('{"z": 1, "a": 1, "m": 1}', '{"z": 1, "a": 2, "m": 2}')
    paths = [u["path"] for u in r["updates"]]
    assert paths == ["$.a", "$.m"]  # ordenado, sin depender del orden de source


def test_diff_list_subtree():
    r = diff_json('["x", "y"]', '["x"]')
    assert r["updates"] == [
        {"op": "update", "path": "$", "value": '["x","y"]', "json": True}
    ]


def test_apply_updates_selected_only():
    dest = '{"value": {"details": {"max": 90, "keep": 1}, "x": [1, 2, 3], "extra": 1}}'
    updates = diff_json(
        '{"value": {"details": {"max": 100, "keep": 1}, "x": [1, 2]}, "q": 9}',
        dest,
    )["updates"]
    merged = apply_updates(dest, updates, selected={"$.value.details.max"})
    assert merged["value"]["details"]["max"] == 100
    assert merged["value"]["x"] == [1, 2, 3]  # no se tocó
    assert "q" not in merged  # insert no seleccionado, no se aplica


def test_apply_updates_all():
    dest = '{"value": {"details": {"max": 90}, "x": [1, 2, 3]}, "extra": 1}'
    updates = diff_json(
        '{"value": {"details": {"max": 100, "nuevo": true}, "x": [1, 2]}}',
        dest,
    )["updates"]
    merged = apply_updates(dest, updates)
    assert merged["value"]["details"]["max"] == 100
    assert merged["value"]["details"]["nuevo"] is True
    assert merged["value"]["x"] == [1, 2]
    assert merged.get("extra") == 1  # data residual del destino se conserva


def test_apply_updates_scalar_root():
    dest = "no-json"
    updates = diff_json("v2", dest)["updates"]
    merged = apply_updates(dest, updates)
    assert merged == "v2"


def test_apply_updates_insert_whole_object():
    dest = '{"a": 1}'
    updates = [{"op": "insert", "path": "$.b", "value": '{"c":[1,2]}', "json": True}]
    merged = apply_updates(dest, updates)
    assert merged == {"a": 1, "b": {"c": [1, 2]}}