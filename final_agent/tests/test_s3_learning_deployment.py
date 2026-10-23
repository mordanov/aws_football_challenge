import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_learning_deployment import config as base_config


def config():
    return {**base_config(), "failure_bucket": "our-owned-failure-archive"}


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
spec = importlib.util.spec_from_file_location(
    "s3_deploy", Path(__file__).resolve().parents[1] / "deploy/s3_learning.py"
)
assert spec is not None and spec.loader is not None
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


def test_s3_variant_uses_only_allowed_services_with_retained_async_failures():
    cfg = config()
    cfg["alarm_topic_arn"] = ""
    value = deploy.template(cfg, "code.zip")
    raw = json.dumps(value)
    for forbidden in ("AWS::DynamoDB", "AWS::SNS", "AWS::SQS", "AWS::Events", "dynamodb:", "sqs:"):
        assert forbidden not in raw
    resources = value["Resources"]
    for name in ("WorkerRole", "LearnerRole"):
        assert resources[name]["Properties"]["RoleName"].startswith("AgentCore-")
    for name in ("Delivery", "LearningDelivery"):
        assert resources[name]["Properties"]["DestinationConfig"]["OnFailure"]["Destination"] == (
            "arn:aws:s3:::" + cfg["failure_bucket"]
        )
    for name in ("Worker", "Learner"):
        env = resources[name]["Properties"]["Environment"]["Variables"]
        assert env["AFC_LEARNING_ENABLED"] == "0"
        assert env["AFC_OWNER_ACCOUNT"] == cfg["account"]
    assert resources["Learner"]["Properties"]["RecursiveLoop"] == "Allow"
    assert all(
        "AlarmActions" not in r["Properties"]
        for r in resources.values()
        if r["Type"] == "AWS::CloudWatch::Alarm"
    )


def test_collector_cannot_write_profile_registry_and_players_are_not_changed():
    value = deploy.template(config(), "code.zip")
    resources = value["Resources"]
    policy = resources["WorkerRole"]["Properties"]["Policies"][0]["PolicyDocument"]
    assert "/registry/" not in json.dumps(policy)
    assert all(r["Type"] != "AWS::BedrockAgentCore::Runtime" for r in resources.values())


def test_failure_archive_owner_privacy_and_encryption_are_checked_before_deployment():
    requests = []
    block = {
        k: True
        for k in (
            "BlockPublicAcls",
            "BlockPublicPolicy",
            "IgnorePublicAcls",
            "RestrictPublicBuckets",
        )
    }
    client = SimpleNamespace(
        head_bucket=lambda **r: requests.append(r),
        get_public_access_block=lambda **r: {"PublicAccessBlockConfiguration": block},
        get_bucket_encryption=lambda **r: {
            "ServerSideEncryptionConfiguration": {
                "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
            }
        },
    )
    deploy.validate_failure_archive(client, config())
    assert requests == [
        {
            "Bucket": config()["failure_bucket"],
            "ExpectedBucketOwner": config()["account"],
        }
    ]
    block["BlockPublicPolicy"] = False
    with pytest.raises(ValueError, match="public access"):
        deploy.validate_failure_archive(client, config())
