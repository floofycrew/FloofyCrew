"""Tests for ``hook/awsenv.py``: pure functions, temp HOME, synthetic configs — no real credential file is ever read."""
from __future__ import annotations

import importlib.util
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("abd_awsenv", ROOT / "hook" / "awsenv.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["abd_awsenv"] = module
    spec.loader.exec_module(module)
    return module


A = _load()

CODEX_BEDROCK = """
model_provider = "amazon-bedrock"
model = "openai.gpt-6-sol"

[model_providers.amazon-bedrock.aws]
region = "us-east-2"
profile = "codex-work"
"""

AWS_CONFIG = """
# comment
[default]
region = us-west-2
output = json

[profile codex-work]
region = us-east-2
credential_process = /opt/tools/vend --account 123456789012 --role admin
s3 =
    max_concurrent_requests = 20

[profile static-only]
region = eu-west-1
aws_access_key_id = NOT-A-REAL-KEY-ID
aws_secret_access_key = not-a-real-value

[profile role]
role_arn = arn:aws:iam::123456789012:role/Dev
source_profile = base

[profile base]
credential_process = /usr/local/bin/vend-base

[profile sso]
sso_session = corp
sso_account_id = 123456789012
sso_role_name = Dev

[sso-session corp]
sso_start_url = https://127.0.0.1:8443/start
sso_region = us-east-1

[profile keyfile]
credential_process = /usr/local/bin/vend-from-key --key ~/.ssh/vend-key

[profile broken]
source_profile = nowhere
"""


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text(CODEX_BEDROCK)
    host_home = home / ".kiro" / "crew"
    host_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    return {"home": str(home), "host_home": str(host_home), "environ": {"HOME": str(home)}}


# --- codex config -----------------------------------------------------------------------------------------


def test_read_codex_config_bedrock(world):
    cfg = A.read_codex_config(os.path.join(world["home"], ".codex", "config.toml"))
    assert cfg.exists and cfg.bedrock and cfg.model_provider == "amazon-bedrock"
    assert cfg.aws_profile == "codex-work" and cfg.aws_region == "us-east-2" and cfg.model == "openai.gpt-6-sol"


def test_read_codex_config_absent_openai_and_broken(tmp_path):
    missing = A.read_codex_config(str(tmp_path / "nope.toml"))
    assert not missing.exists and not missing.bedrock and missing.provider_kind == ""
    p = tmp_path / "openai.toml"
    p.write_text('model = "gpt-5"\n')
    assert A.read_codex_config(str(p)).provider_kind == "openai"
    b = tmp_path / "bad.toml"
    b.write_text("model_provider = [unterminated\n")
    bad = A.read_codex_config(str(b))
    assert bad.exists and bad.parse_error and not bad.bedrock


def test_codex_home_honours_override():
    assert A.codex_home({"CODEX_HOME": "/x/codex"}, "/home/u") == "/x/codex"
    assert A.codex_home({}, "/home/u") == "/home/u/.codex"


# --- aws config parsing and the profile plan ----------------------------------------------------------------


def test_parse_aws_config_sections_and_nested_blocks():
    sections = A.parse_aws_config(AWS_CONFIG)
    assert sections["default"] == {"region": "us-west-2", "output": "json"}
    assert sections["profile codex-work"]["credential_process"].startswith("/opt/tools/vend")
    assert "max_concurrent_requests" not in sections["profile codex-work"], "nested s3 block lines are skipped"
    assert "s3" in sections["profile codex-work"]
    assert sections["sso-session corp"]["sso_region"] == "us-east-1"


def test_plan_profile_process_chain_and_region_default():
    plan = A.plan_profile(A.parse_aws_config(AWS_CONFIG), "codex-work", region_default="us-east-2")
    assert plan.found and plan.credential_source == "process" and plan.chain == ["profile codex-work"]
    assert plan.process_may_need_hidden_dir == "" and plan.static_keys_dropped == {}
    rendered = A.render_config(plan)
    assert "[profile codex-work]" in rendered and "credential_process = /opt/tools/vend" in rendered
    assert "[default]" not in rendered, "only the selected chain is carried"
    assert "max_concurrent_requests" not in rendered


def test_plan_profile_drops_static_keys_and_names_them_only():
    plan = A.plan_profile(A.parse_aws_config(AWS_CONFIG), "static-only")
    assert plan.found and plan.credential_source == "static_only"
    assert plan.static_keys_dropped == {"profile static-only": ["aws_access_key_id", "aws_secret_access_key"]}
    rendered = A.render_config(plan)
    assert "AKIA" not in rendered and "not-a-real-value" not in rendered and "aws_secret_access_key" not in rendered
    assert plan.to_dict()["keys"] == {"profile static-only": ["region"]}


def test_plan_profile_follows_source_profile_and_sso_session():
    sections = A.parse_aws_config(AWS_CONFIG)
    role = A.plan_profile(sections, "role")
    assert role.chain == ["profile role", "profile base"] and role.credential_source == "assume_role"
    assert role.credential_process == "/usr/local/bin/vend-base"
    sso = A.plan_profile(sections, "sso")
    assert sso.chain == ["profile sso", "sso-session corp"] and sso.credential_source == "sso"
    assert "[sso-session corp]" in A.render_config(sso)
    broken = A.plan_profile(sections, "broken")
    assert broken.found and broken.missing == ["profile nowhere"]
    assert not A.plan_profile(sections, "ghost").found


def test_plan_profile_hints_when_the_process_command_line_names_a_hidden_dir():
    plan = A.plan_profile(A.parse_aws_config(AWS_CONFIG), "keyfile")
    assert plan.credential_source == "process" and plan.process_may_need_hidden_dir == ".ssh"
    explicit = A.plan_profile(A.parse_aws_config("[profile p]\ncredential_process = cat ~/.aws/thing\n"), "p")
    assert explicit.process_may_need_hidden_dir == ".aws"
    # a vend whose binary lives elsewhere and names no masked directory gets no hint, whatever it is called
    quiet = A.plan_profile(A.parse_aws_config("[profile q]\ncredential_process = /opt/vendor/bin/vend credential-process\n"), "q")
    assert quiet.process_may_need_hidden_dir == ""


# --- materialize + preflight ------------------------------------------------------------------------------------


def test_materialize_writes_private_files(world):
    plan = A.plan_profile(A.parse_aws_config(AWS_CONFIG), "codex-work")
    paths = A.materialize(world["host_home"], plan)
    assert paths["dir"] == os.path.join(world["host_home"], "codex-aws")
    assert stat.S_IMODE(os.stat(paths["dir"]).st_mode) == 0o700
    for name in ("config", "credentials"):
        assert stat.S_IMODE(os.stat(paths[name]).st_mode) == 0o600
    assert os.path.getsize(paths["credentials"]) == 0
    # a second run is idempotent; a credentials file something else filled is truncated, never kept
    Path(paths["credentials"]).write_text("aws_access_key_id = planted\n")
    A.materialize(world["host_home"], plan)
    assert os.path.getsize(paths["credentials"]) == 0


def test_preflight_ok_builds_the_env(world):
    pf = A.preflight(home=world["home"], host_home=world["host_home"], environ=world["environ"], aws_config_text=AWS_CONFIG)
    assert pf.ok and pf.verdict == "ok"
    assert pf.env["AWS_PROFILE"] == "codex-work" and pf.env["AWS_DEFAULT_PROFILE"] == "codex-work"
    assert pf.env["AWS_CONFIG_FILE"].endswith("/codex-aws/config") and pf.env["AWS_SHARED_CREDENTIALS_FILE"].endswith("/codex-aws/credentials")
    assert pf.files["config"]["private"] and pf.files["credentials"]["writable"]
    assert "AKIA" not in str(pf.to_dict())


def test_preflight_verdicts(world):
    home, host_home, env = world["home"], world["host_home"], world["environ"]
    os.remove(os.path.join(home, ".codex", "config.toml"))
    assert A.preflight(home=home, host_home=host_home, environ=env, aws_config_text=AWS_CONFIG).verdict == "no_codex_config"
    Path(home, ".codex", "config.toml").write_text('model = "gpt-5"\n')
    pf = A.preflight(home=home, host_home=host_home, environ=env, aws_config_text=AWS_CONFIG)
    assert pf.ok and pf.verdict == "not_bedrock" and pf.env == {}
    for profile, verdict in (("static-only", "static_only"), ("broken", "chain_broken"), ("ghost", "profile_missing")):
        Path(home, ".codex", "config.toml").write_text(CODEX_BEDROCK.replace("codex-work", profile))
        pf = A.preflight(home=home, host_home=host_home, environ=env, aws_config_text=AWS_CONFIG)
        assert (pf.ok, pf.verdict) == (False, verdict), profile
    Path(home, ".codex", "config.toml").write_text(CODEX_BEDROCK.replace("codex-work", "keyfile"))
    pf = A.preflight(home=home, host_home=host_home, environ=env, aws_config_text=AWS_CONFIG)
    assert pf.ok and pf.verdict == "process_may_need_hidden_dir" and ".ssh" in pf.sandbox_note and pf.env["AWS_PROFILE"] == "keyfile"
    assert "sandboxed probe" in pf.sandbox_note, "a hint, not a verdict: the probe decides"
    Path(home, ".codex", "config.toml").write_text('model_provider = "amazon-bedrock"\n[model_providers.amazon-bedrock.aws]\nregion = "us-east-1"\n')
    pf = A.preflight(home=home, host_home=host_home, environ=env, aws_config_text=AWS_CONFIG)
    assert pf.verdict == "no_credential_source" and pf.profile["profile"] == "default", "no profile named → default, which has no source here"


def test_preflight_report_only_does_not_write(world):
    pf = A.preflight(home=world["home"], host_home=world["host_home"], environ=world["environ"], write=False, aws_config_text=AWS_CONFIG)
    assert not pf.ok and pf.verdict == "not_applied" and not os.path.exists(os.path.join(world["host_home"], "codex-aws", "config"))


def test_preflight_fills_the_region_from_the_codex_config_when_the_profile_has_none(world):
    """The shape seen on the test box: the profile carries only credential_process; the region lives in config.toml."""
    bare = "[profile codex-work]\ncredential_process = /opt/tools/vend --role admin\n"
    pf = A.preflight(home=world["home"], host_home=world["host_home"], environ=world["environ"], aws_config_text=bare)
    assert pf.ok and pf.verdict == "ok" and pf.profile["keys"] == {"profile codex-work": ["credential_process", "region"]}
    text = Path(world["host_home"], "codex-aws", "config").read_text()
    assert "region = us-east-2" in text and text.count("[profile codex-work]") == 1


def test_preflight_reads_the_source_config_when_none_is_handed_in(world):
    aws = Path(world["home"], ".aws")
    aws.mkdir()
    (aws / "config").write_text(AWS_CONFIG)
    pf = A.preflight(home=world["home"], host_home=world["host_home"], environ=world["environ"])
    assert pf.ok and pf.verdict == "ok"
    (aws / "config").unlink()
    pf = A.preflight(home=world["home"], host_home=world["host_home"], environ=world["environ"])
    assert pf.verdict == "profile_missing" and "cannot read" in pf.detail


# --- classifier --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("message, category, repairable", [
    ("failed to load AWS credentials: the credential provider was not enabled", "credentials", True),
    ("Authentication required: failed to load AWS credentials: Error retrieving credentials: external process exited with code 1", "credential_process", False),
    ("User is unauthenticated. Run the sign-in tool to authenticate", "credential_process", False),
    ("The security token included in the request is expired (ExpiredTokenException)", "endpoint_auth", False),
    ("ThrottlingException: Too many requests, please wait before trying again", "throttle", False),
    ("request timed out after 300s", "timeout", False),
    ("ValidationException: The provided model identifier is invalid", "model", False),
    ("502 Bad Gateway", "server", False),
    ("stream closed by peer; broken pipe", "stream_cleanup", False),
    ("DispatchFailure: connection refused", "http", False),
    ("something odd", "error", False),
])
def test_classify_failure(message, category, repairable):
    out = A.classify_failure(message)
    assert (out["category"], out["repairable"]) == (category, repairable), out


def test_classify_failure_transient_and_reached_model():
    assert A.classify_failure("weird", transient=True)["category"] == "transient"
    late = A.classify_failure("failed to load AWS credentials: the credential provider was not enabled", reached_model=True)
    assert late["category"] == "endpoint_auth" and late["repairable"] is False, "credentials were accepted once the model answered; never refresh for that"


def test_redact_strips_secret_shapes():
    # every credential shape is assembled from adjacent literals at runtime, so the shipped source carries no
    # bare credential word and no key-shaped literal: credential scanners (GitHub's push protection included)
    # reject the shape on sight, made-up or not, and the public export's own gate (scripts/export_public.py) too
    key_id = "AKIA" + "ABCDEFGHIJKLMNOP"
    secret_key = "abcdefghijklmnopqrstuvwxyz" + "0123456789ABCD"  # 40 base64 characters, the length of a real one
    text = f"aws_secret_access_key={secret_key} {key_id} " + "Bea" "rer eyJhbGciOiJIUzI1NiJ9.xxxxxxxxxxxx " + "A" * 60 + " and a message"
    out = A.redact(text)
    assert key_id not in out and "abcdefghijklmnopqrstuvwxyz" not in out and "eyJhbGci" not in out and "A" * 60 not in out
    assert "and a message" in out and len(A.redact("word " * 300)) == 400
