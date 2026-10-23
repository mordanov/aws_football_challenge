from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch

import botocore.session
import pytest
from botocore.validate import validate_parameters

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("learning_deploy", ROOT / "deploy/learning.py")
assert spec is not None and spec.loader is not None
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


def config():
    return {
        "account": "123456789012",
        "region": "us-east-1",
        "team_id": "our_team",
        "stack_name": "afc-learning-test",
        "memory_id": "Memory_ourteam-abcdefghij",
        "artifact_bucket": "our-private-artifact-bucket",
        "alarm_topic_arn": "arn:aws:sns:us-east-1:123456789012:monitoring",
        "collection_enabled": False,
        "log_groups": [f"/aws/bedrock-agentcore/runtimes/our-seat-{i}-DEFAULT" for i in range(5)],
    }


def test_template_isolated_worker_permissions_and_durable_disabled_by_default():
    value = deploy.template(config(), "sha256.zip")
    resources = value["Resources"]
    subs = [r for r in resources.values() if r["Type"] == "AWS::Logs::SubscriptionFilter"]
    assert len(subs) == 5
    assert all(r["Condition"] == "Collect" for r in subs)
    assert value["Conditions"]["Collect"] == {"Fn::Equals": [{"Ref": "CollectionEnabled"}, "true"]}
    assert value["Parameters"]["CollectionEnabled"]["Default"] == "false"
    assert all(
        resources[name]["DeletionPolicy"] == "Retain" for name in ("Registry", "Outbox", "Failures")
    )
    assert all(
        "TimeToLiveSpecification" not in resources[name]["Properties"]
        for name in ("Registry", "Outbox")
    )
    role = resources["WorkerRole"]["Properties"]
    policy = json.dumps(role["Policies"])
    assert "CreateEvent" in policy and "Outbox" in policy
    assert "Registry" not in policy
    assert "InvokeModel" not in policy and "UpdateAgentRuntime" not in policy
    assert resources["Worker"]["Properties"]["ReservedConcurrentExecutions"] == 2
    assert resources["Delivery"]["Properties"]["DestinationConfig"]["OnFailure"]
    assert value["Outputs"]["RegistryTable"] and value["Outputs"]["PlayerPolicyArn"]
    # The SDK schema catches template-body/capability request mistakes without AWS access.
    shape = (
        botocore.session.get_session()
        .get_service_model("cloudformation")
        .operation_model("CreateStack")
        .input_shape
    )
    validate_parameters(
        {
            "StackName": "afc-learning-test",
            "TemplateBody": json.dumps(value),
            "Capabilities": ["CAPABILITY_IAM"],
        },
        shape,
    )


@pytest.mark.parametrize("damage", ["foreign_topic", "extra_group", "bad_enabled", "unsafe_team"])
def test_invalid_or_foreign_configuration_is_rejected_before_provisioning(damage):
    value = config()
    if damage == "foreign_topic":
        value["alarm_topic_arn"] = "arn:aws:sns:us-west-2:999999999999:topic"
    elif damage == "extra_group":
        value["log_groups"].append("/untrusted")
    elif damage == "bad_enabled":
        value["collection_enabled"] = "false"
    else:
        value["team_id"] = "../other-team"
    with pytest.raises(ValueError):
        deploy.template(value, "sha256.zip")


def test_package_is_self_contained_including_current_memory_sdk(tmp_path):
    archive = tmp_path / "learning.zip"
    digest = deploy.package(archive)
    assert len(digest) == 64
    with zipfile.ZipFile(archive) as bundle:
        assert "agent/learning_collector.py" in bundle.namelist()
        assert "boto3/__init__.py" in bundle.namelist()
        assert any(
            name.startswith("botocore/data/bedrock-agentcore/")
            and name.endswith("/service-2.json.gz")
            for name in bundle.namelist()
        )
        assert not any(
            "__pycache__" in name or ".." in Path(name).parts for name in bundle.namelist()
        )
        extracted = tmp_path / "lambda"
        bundle.extractall(extracted)
    probe = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            "import agent.learning_collector, boto3, botocore.session; "
            "s=botocore.session.get_session().get_service_model('bedrock-agentcore'); "
            "assert 'extractionMode' in s.operation_model('CreateEvent').input_shape.members",
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(extracted)},
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stderr


def test_identical_source_packages_have_identical_content_keys(tmp_path):
    with patch("zipfile.time.localtime", return_value=(2026, 1, 1, 0, 0, 0, 0, 1, 0)):
        first = deploy.package(tmp_path / "first.zip")
    with patch("zipfile.time.localtime", return_value=(2026, 1, 2, 0, 0, 0, 0, 1, 0)):
        second = deploy.package(tmp_path / "second.zip")
    assert first == second


def test_write_commands_without_approval_fail_before_loading_aws_sdk(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config()))
    for command in ("apply", "bootstrap"):
        result = subprocess.run(
            [
                sys.executable,
                "-S",
                str(ROOT / "deploy/learning.py"),
                command,
                "--config",
                str(path),
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
        assert "AWS writes require --approve" in result.stderr
        assert "ModuleNotFoundError" not in result.stderr


def test_apply_validates_config_before_touching_aws():
    value = config()
    value["collection_enabled"] = "false"
    with patch.object(deploy, "identity", side_effect=AssertionError("AWS accessed")):
        with pytest.raises(ValueError, match="collection_enabled"):
            deploy.apply(value)
