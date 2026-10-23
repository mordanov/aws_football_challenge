"""Provision the permitted S3/Memory variant; AWS writes require --approve."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import learning

BASE_TEMPLATE = learning.template


def template(config: dict, code_key: str) -> dict:
    value = BASE_TEMPLATE(config, code_key)
    resources = value["Resources"]
    for key in (
        "Registry",
        "Outbox",
        "Failures",
        "PlayerPolicy",
        "AlarmTopic",
        "LearningSchedule",
        "LearningInvokePermission",
    ):
        resources.pop(key, None)
    account, region, team, bucket, stack = (
        config[k] for k in ("account", "region", "team_id", "artifact_bucket", "stack_name")
    )
    base = f"arn:aws:s3:::{bucket}"
    prefix = f"final-agent-learning/{team}"
    memory = f"arn:aws:bedrock-agentcore:{region}:{account}:memory/{config['memory_id']}"
    if not config.get("failure_bucket") or config["failure_bucket"] == bucket:
        raise ValueError("S3 learning requires a separate owned failure_bucket")
    failure_arn = "arn:aws:s3:::" + config["failure_bucket"]
    learner_arn = f"arn:aws:lambda:{region}:{account}:function:{stack}-learner"
    enabled = "1" if config.get("learning_enabled", False) else "0"
    for role_key, function_key, entry, label in (
        ("WorkerRole", "Worker", "collector", "collector"),
        ("LearnerRole", "Learner", "learner", "learner"),
    ):
        name = stack + "-" + label
        role = resources[role_key]["Properties"]
        role["RoleName"] = "AgentCore-" + name
        statements = [
            {
                "Effect": "Allow",
                "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                "Resource": f"arn:aws:logs:{region}:{account}:log-group:/aws/lambda/{name}:*",
            },
            {
                "Effect": "Allow",
                "Action": ["s3:ListBucket"],
                "Resource": [base, failure_arn],
                "Condition": {"StringEquals": {"s3:ResourceAccount": account}},
            },
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject"],
                "Resource": (
                    [base + f"/{prefix}/*"]
                    if label == "learner"
                    else [
                        base + f"/{prefix}/observations/*",
                        base + f"/{prefix}/acknowledgements/*",
                        base + f"/{prefix}/work.json",
                    ]
                ),
                "Condition": {"StringEquals": {"s3:ResourceAccount": account}},
            },
            {
                "Effect": "Allow",
                "Action": "s3:PutObject",
                "Resource": {"Fn::Join": ["", [failure_arn, "/*"]]},
                "Condition": {"StringEquals": {"s3:ResourceAccount": account}},
            },
            {
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:CreateEvent"]
                if label == "collector"
                else [
                    "bedrock-agentcore:ListEvents",
                    "bedrock-agentcore:CreateEvent",
                ],
                "Resource": memory,
            },
            {"Effect": "Allow", "Action": "lambda:InvokeFunction", "Resource": learner_arn},
        ]
        role["Policies"] = [
            {
                "PolicyName": "S3BoundedLearning",
                "PolicyDocument": {"Version": "2012-10-17", "Statement": statements},
            }
        ]
        function = resources[function_key]["Properties"]
        function["Handler"] = "agent.s3_worker." + entry
        function["Environment"]["Variables"] = {
            "AFC_TEAM_ID": team,
            "AFC_MEMORY_ID": config["memory_id"],
            "AFC_LEARNING_BUCKET": bucket,
            "AFC_OWNER_ACCOUNT": account,
            "AFC_LEARNER_FUNCTION": stack + "-learner",
            "AFC_LEARNING_ENABLED": enabled,
        }
        if label == "collector":
            function["Environment"]["Variables"]["AFC_LOG_GROUPS"] = json.dumps(
                config["log_groups"]
            )
        else:
            function["RecursiveLoop"] = "Allow"
    for key in ("Delivery", "LearningDelivery"):
        resources[key]["Properties"]["MaximumRetryAttempts"] = 2
        resources[key]["Properties"]["DestinationConfig"] = {
            "OnFailure": {"Destination": failure_arn}
        }
    resources.pop("DeadLetters")
    for resource in resources.values():
        if resource["Type"] == "AWS::CloudWatch::Alarm":
            resource["Properties"].pop("AlarmActions", None)
    resources["LearnerDestinationFailures"] = {
        "Type": "AWS::CloudWatch::Alarm",
        "Properties": {
            "Namespace": "AWS/Lambda",
            "MetricName": "DestinationDeliveryFailures",
            "Dimensions": [{"Name": "FunctionName", "Value": {"Ref": "Learner"}}],
            "Statistic": "Sum",
            "Period": 60,
            "EvaluationPeriods": 1,
            "Threshold": 0,
            "ComparisonOperator": "GreaterThanThreshold",
            "TreatMissingData": "notBreaching",
        },
    }
    value["Description"] = "final_agent S3 coordination and shared AgentCore Memory learning"
    value["Outputs"] = {
        "LearningBucket": {"Value": bucket},
        "FailureArchive": {"Value": config["failure_bucket"]},
        "Collector": {"Value": {"Ref": "Worker"}},
        "Learner": {"Value": {"Ref": "Learner"}},
    }
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "apply", "bootstrap"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--approve", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    learning.checked_config(config)
    template(config, "PREFLIGHT.zip")
    if args.command == "plan":
        print(json.dumps(template(config, "PACKAGED_SHA256.zip"), indent=2))
        return
    if not args.approve:
        parser.error("AWS writes require --approve")
    if args.command == "apply":
        validate_failure_archive(learning.identity(config).client("s3"), config)
        learning.apply(config, template_builder=template, named_iam=True)
        return
    import sys

    sys.path.insert(0, str(learning.ROOT))
    from agent.profile import load_profile
    from agent.s3_registry import S3ProfileRegistry

    session = learning.identity(config)
    result = S3ProfileRegistry(
        session.client("s3"),
        bucket=config["artifact_bucket"],
        team_id=config["team_id"],
        owner_account=config["account"],
    ).bootstrap(load_profile(learning.ROOT / "profiles/recommended.json"))
    print(json.dumps({"bootstrapped_digest": result}))


def validate_failure_archive(client, config: dict) -> None:
    request = {"Bucket": config["failure_bucket"], "ExpectedBucketOwner": config["account"]}
    client.head_bucket(**request)
    block = client.get_public_access_block(**request)["PublicAccessBlockConfiguration"]
    if not all(
        block.get(name) is True
        for name in (
            "BlockPublicAcls",
            "BlockPublicPolicy",
            "IgnorePublicAcls",
            "RestrictPublicBuckets",
        )
    ):
        raise ValueError("failure archive must block all public access")
    rules = client.get_bucket_encryption(**request)["ServerSideEncryptionConfiguration"]["Rules"]
    if not rules or any(
        rule["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"] != "AES256" for rule in rules
    ):
        raise ValueError("failure archive requires default AES256 encryption")


if __name__ == "__main__":
    main()
