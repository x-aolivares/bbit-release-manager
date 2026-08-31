from src.scan.params import classify_ssm, extract_ssm_params


def test_extract_bare_path_in_interpolation():
    lines = ["abc.xyz.mbv=${/config/common/xyz/abc/mbv}"]
    assert extract_ssm_params(lines, ["/config", "/common"]) == [
        ("/config/common/xyz/abc/mbv", ""),
    ]


def test_extract_quoted_paths():
    lines = [
        'v1="/config/common/xyz/abc/mbv"',
        "v2='/config/common/xyz/abc/mbv'",
        "v3=/config/common/xyz/abc/mbv",
    ]
    assert extract_ssm_params(lines, ["/config", "/common"]) == [
        ("/config/common/xyz/abc/mbv", ""),
    ]


def test_extract_resolve_ssm_keeps_leading_slash():
    lines = ['  host: "{{resolve:ssm:/config/database/host:1234}}"']
    assert extract_ssm_params(lines, ["/config", "/common"]) == [
        ("/config/database/host", "1234"),
    ]


def test_extract_resolve_and_quoted_combined():
    lines = [
        '  host: "{{resolve:ssm:/config/database/host:1234}}"',
        "  fallback: '/config/database/host'",
    ]
    assert extract_ssm_params(lines, ["/config"]) == [
        ("/config/database/host", ""),
        ("/config/database/host", "1234"),
    ]


def test_extract_ignores_python_paths_and_urls():
    lines = [
        "import os",
        "p = '/usr/bin/python3'",
        "q = '/var/log/app.log'",
        "u = 'https://example.com/config/common/x'",
        "w = '/tmp/cache'",
        "sys.path.append('/home/user/src')",
    ]
    assert extract_ssm_params(lines, ["/config", "/common"]) == []


def test_extract_ignores_paths_outside_prefixes():
    lines = ["x=${/k8s/secret/foo}"]
    assert extract_ssm_params(lines, ["/config", "/common"]) == []


def test_extract_dedup():
    lines = [
        'a="${/config/x}"',
        'b="${/config/x}"',
        'c="{{resolve:ssm:/config/x:1}}"',
    ]
    assert extract_ssm_params(lines, ["/config"]) == [
        ("/config/x", ""),
        ("/config/x", "1"),
    ]


def test_extract_no_prefixes_matches_all_slash_paths():
    lines = ['v="${/any/param/x}"']
    assert extract_ssm_params(lines, []) == [("/any/param/x", "")]


def test_extract_no_match():
    assert extract_ssm_params(["sin refs"], ["/config"]) == []


def test_classify_nuevo_vs_reutilizado():
    origin = {
        "abc": {"/config/common/xyz/abc/mbv", "/config/new/one"},
    }
    dest = {
        "xyz": {"/config/common/xyz/abc/mbv"},
        "abc": {"/config/old/two"},
    }
    tipo = classify_ssm(origin, dest)
    assert tipo == {
        "/config/common/xyz/abc/mbv": "reutilizado",
        "/config/new/one": "nuevo",
    }


def test_classify_repeated_repositivo():
    origin = {"abc": {"/config/common/xyz/abc/mbv"}, "xyz": {"/config/common/xyz/abc/mbv"}}
    dest = {"xyz": {"/config/common/xyz/abc/mbv"}}
    assert classify_ssm(origin, dest) == {"/config/common/xyz/abc/mbv": "reutilizado"}