from src.scan.params import extract_ssm_params


def test_extract_ssm_params_filters_by_prefix():
    lines = [
        '  host: "{{resolve:ssm:/config/database/host:1234}}"',
        '  cluster: "{{resolve:ssm:/common/cache/cluster:5678}}"',
        '  key: "{{resolve:ssm:/k8s/secret:99}}"',
    ]
    params = extract_ssm_params(lines, ["/config", "/common"])
    assert params == [
        ("common/cache/cluster", "5678"),
        ("config/database/host", "1234"),
    ]


def test_extract_ssm_params_dedup():
    lines = [
        'a="{{resolve:ssm:/config/x:1}}"',
        'b="{{resolve:ssm:/config/x:1}}"',
    ]
    assert extract_ssm_params(lines, ["/config"]) == [("config/x", "1")]


def test_extract_ssm_params_no_prefixes():
    lines = ['"{{resolve:ssm:/any/param:2}}"']
    assert extract_ssm_params(lines, []) == [("any/param", "2")]


def test_extract_ssm_params_no_match():
    assert extract_ssm_params(["sin refs"], ["/config"]) == []