from bbit_release.scan.params import classify_ssm, extract_ssm_params


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


def test_extract_resolve_requires_segment_boundary():
    lines = [
        'a="{{resolve:ssm:/configfoo/db:1}}"',
        'b="{{resolve:ssm:/commonwealth/x}}"',
        'c="{{resolve:ssm:/config_1/y:2}}"',
    ]
    assert extract_ssm_params(lines, ["/config", "/common"]) == []


def test_extract_bare_requires_segment_boundary():
    lines = [
        "a=/configfoo/db",
        "b=/commonwealth/x",
        "c=/config_1/y",
    ]
    assert extract_ssm_params(lines, ["/config", "/common"]) == []


def test_extract_prefix_exact_slug_matches():
    lines = ['a="{{resolve:ssm:/config:h}}"']
    assert extract_ssm_params(lines, ["/config", "/common"]) == [
        ("/config", "h"),
    ]


def test_extract_resolve_exact_slug_excluded_by_bare_rule():
    lines = ['a="{{resolve:ssm:/configfoo/x:1}}"']
    assert extract_ssm_params(lines, ["/config"]) == []


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
    global_dest = {"/config/common/xyz/abc/mbv", "/config/old/two"}
    tipo = classify_ssm(origin, global_dest)
    assert tipo == {
        "/config/common/xyz/abc/mbv": "reutilizado",
        "/config/new/one": "nuevo",
    }


def test_classify_repeated_repositivo():
    origin = {"abc": {"/config/common/xyz/abc/mbv"}, "xyz": {"/config/common/xyz/abc/mbv"}}
    global_dest = {"/config/common/xyz/abc/mbv"}
    assert classify_ssm(origin, global_dest) == {"/config/common/xyz/abc/mbv": "reutilizado"}


def test_classify_reused_from_repo_without_branch():
    """Un param en release de 'abc' ya productivo en master de 'xyz' (sin la
    rama origen) debe salir reutilizado, no nuevo."""
    origin = {"abc": {"/config/shared/secret"}}
    global_dest = {"/config/shared/secret", "/config/other"}
    assert classify_ssm(origin, global_dest) == {"/config/shared/secret": "reutilizado"}