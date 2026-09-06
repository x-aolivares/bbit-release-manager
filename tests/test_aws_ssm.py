"""Tests de AWS session/SSM sin red (boto3 fake inyectado en la sesión)."""

from types import SimpleNamespace

import pytest

from bbit_release.aws.session import AwsSession, AwsSessionError
from bbit_release.aws.ssm import enrich_diff_params, fetch_values
from bbit_release.cache import get_cache, reset_cache


@pytest.fixture(autouse=True)
def _clean_cache(tmp_path):
    reset_cache()
    cache = get_cache(tmp_path / "test_aws_ssm.db")
    yield cache
    cache.clear_ssm_values()
    reset_cache()


class FakeParamsResponse:
    """Emula la respuesta de ssm.get_parameters (Parameters/InvalidParameters)."""

    def __init__(self, values: dict[str, str], missing: list[str] | None = None,
                 types: dict[str, str] | None = None):
        self.values = values
        self.missing = missing or []
        self.types = types or {}

    def __call__(self, **kwargs):
        names = kwargs.get("Names") or []
        params = []
        invalid = []
        for n in names:
            if n in self.values:
                ptype = self.types.get(n, "String")
                params.append({"Name": n, "Value": self.values[n], "Type": ptype})
            else:
                invalid.append(n)
        return {"Parameters": params, "InvalidParameters": invalid}


class FakeSsmClient:
    def __init__(self, response=None, batches=None):
        self.response = response
        self.calls: list[dict] = []
        self.batches = batches or []

    def get_parameters(self, **kwargs):
        self.calls.append(kwargs)
        if self.batches:
            resp = self.batches.pop(0)
            return resp(**kwargs) if callable(resp) else resp
        if self.response is None:
            return {"Parameters": [], "InvalidParameters": []}
        return self.response(**kwargs) if callable(self.response) else self.response


class FakeBotoSession:
    def __init__(self, ssm=None, sts=None):
        self._ssm = ssm or FakeSsmClient()
        self._sts = sts
        self.profile_name = None
        self.region_name = None

    def client(self, service, **kwargs):
        self.region_name = kwargs.get("region_name")
        if service == "ssm":
            return self._ssm
        if service == "sts":
            if self._sts is None:
                return SimpleNamespace(
                    get_caller_identity=lambda: {
                        "Arn": "arn:aws:iam::123456789012:role/release",
                        "Account": "123456789012",
                        "UserId": "AIDAEXAMPLE",
                    }
                )
            return self._sts
        raise AssertionError(f"servicio inesperado: {service}")


def _session(**overrides) -> AwsSession:
    kwargs = {
        "profile": "release-qa",
        "region": "us-east-1",
        "client_id": "c-1",
    }
    kwargs.update(overrides)
    return AwsSession(**kwargs)


def _inject(session: AwsSession, ssm: FakeSsmClient | None = None) -> FakeBotoSession:
    fake = FakeBotoSession(ssm=ssm)
    session._session = fake
    return fake


# -- AwsSession ----------------------------------------------------------------


def test_status_returns_identity_without_secrets(_clean_cache):
    session = _session()
    _inject(session)
    status = session.status()
    assert status["profile"] == "release-qa"
    assert status["region"] == "us-east-1"
    assert status["account"] == "123456789012"
    assert "role/release" in status["arn"]
    # nunca exponer tokens/secretos
    assert "token" not in status and "secret" not in status


def test_status_invalid_credentials_raises_hint(_clean_cache):
    session = _session()
    _inject(session, ssm=None)
    # forzar error de STS con un client que explota
    class BoomSts:
        def get_caller_identity(self):
            raise RuntimeError("profile expired")

    fake = FakeBotoSession(ssm=None, sts=BoomSts())
    session._session = fake
    with pytest.raises(AwsSessionError) as exc:
        session.status()
    assert "aws sso login" in str(exc.value)


def test_available_only_with_profile(_clean_cache):
    assert _session(profile="x").available is True
    assert _session(profile="").available is False


def test_default_region_us_east_1(_clean_cache):
    s = _session(region="")
    assert s.region == "us-east-1"


# -- fetch_values -------------------------------------------------------------

def test_fetch_values_batches_of_10(_clean_cache):
    ssm = FakeSsmClient()
    session = _session()
    _inject(session, ssm)
    paths = [f"/config/app/{i}" for i in range(15)]
    values = {p: f"v{i}" for i, p in enumerate(paths)}
    ssm.response = FakeParamsResponse(values)

    result = fetch_values(session, paths, cache=get_cache())
    # 15 paths -> 2 llamadas (10 + 5)
    assert len(ssm.calls) == 2
    assert len(ssm.calls[0]["Names"]) == 10
    assert len(ssm.calls[1]["Names"]) == 5
    assert result["/config/app/0"]["value"] == "v0"


def test_fetch_values_marks_missing(_clean_cache):
    ssm = FakeSsmClient()
    session = _session()
    _inject(session, ssm)
    ssm.response = FakeParamsResponse(
        {"/config/a": "1"},
        missing=["/config/nope"],
    )
    result = fetch_values(session, ["/config/a", "/config/nope"], cache=get_cache())
    assert "/config/a" in result
    assert "/config/nope" not in result


def test_fetch_values_caches_and_reuses(_clean_cache):
    cache = get_cache()
    ssm = FakeSsmClient()
    session = _session()
    _inject(session, ssm)
    ssm.response = FakeParamsResponse({"/config/a": "1"})

    first = fetch_values(session, ["/config/a"], cache=cache)
    assert first["/config/a"]["value"] == "1"
    assert len(ssm.calls) == 1

    second = fetch_values(session, ["/config/a"], cache=cache)
    assert second["/config/a"]["value"] == "1"
    assert len(ssm.calls) == 1  # sin llamada extra -> cache hit

    # otra sesión (distinta client_id) SI consulta a AWS (cache por cliente)
    ssm2 = FakeSsmClient()
    ssm2.response = FakeParamsResponse({"/config/a": "1"})
    session_b = _session(client_id="c-2")
    _inject(session_b, ssm2)
    fetch_values(session_b, ["/config/a"], cache=cache)
    assert len(ssm2.calls) == 1


def test_fetch_values_per_client_decrypt_isolation(_clean_cache):
    cache = get_cache()
    # Juan guarda el valor DESENCRIPTADO (decrypt=True)
    ssm_juan = FakeSsmClient()
    juan = _session(client_id="juan")
    _inject(juan, ssm_juan)
    ssm_juan.response = FakeParamsResponse({"/config/secret": "SEÑO"}, types={"/config/secret": "SecureString"})
    fetch_values(juan, ["/config/secret"], decrypt=True, cache=cache)

    # María pide sin decrypt: NO ve el valor de Juan (key diferente)
    ssm_maria = FakeSsmClient()
    maria = _session(client_id="maria")
    _inject(maria, ssm_maria)
    ssm_maria.response = FakeParamsResponse({})
    result = fetch_values(maria, ["/config/secret"], decrypt=False, cache=cache)
    assert "/config/secret" not in result
    assert len(ssm_maria.calls) == 1

    # Y María con decrypt=True tampoco ve la cache de Juan (por client_id)
    result2 = fetch_values(maria, ["/config/secret"], decrypt=True, cache=cache)
    assert "/config/secret" not in result2


# -- enrich_diff_params -------------------------------------------------------

def _params(*paths):
    return [{"param": p, "arn": "", "tipo": "nuevo", "qa_value": None, "repos": ["r1"], "count": 1}
            for p in paths]


def test_enrich_skipped_without_session():
    params = _params("/config/a")
    out = enrich_diff_params(params, None)
    assert out[0]["aws_status"] == "skipped"
    assert out[0]["qa_value"] is None


def test_enrich_skipped_when_no_profile():
    params = _params("/config/a")
    s = _session(profile="")
    out = enrich_diff_params(params, s, cache=get_cache())
    assert out[0]["aws_status"] == "skipped"


def test_enrich_ok_and_missing(_clean_cache):
    cache = get_cache()
    ssm = FakeSsmClient()
    session = _session()
    _inject(session, ssm)
    ssm.response = FakeParamsResponse(
        {"/config/a": "valor-qa"},
        missing=["/config/nope"],
    )
    params = _params("/config/a", "/config/nope")
    out = enrich_diff_params(params, session, cache=cache)
    by_path = {p["param"]: p for p in out}
    assert by_path["/config/a"]["aws_status"] == "ok"
    assert by_path["/config/a"]["qa_value"] == "valor-qa"
    assert by_path["/config/nope"]["aws_status"] == "missing"
    assert by_path["/config/nope"]["qa_value"] is None


def test_enrich_decrypt_flag_controls_fetch(_clean_cache):
    cache = get_cache()
    ssm = FakeSsmClient()
    session = _session()
    _inject(session, ssm)
    ssm.response = FakeParamsResponse(
        {"/config/secret": "PLANO"},
        types={"/config/secret": "SecureString"},
    )
    params = _params("/config/secret")
    out = enrich_diff_params(params, session, decrypt=True, cache=cache)
    assert out[0]["aws_status"] == "ok"
    assert out[0]["qa_value"] == "PLANO"
    assert ssm.calls[0]["WithDecryption"] is True