"""Package/provision learning foundations; AWS writes require explicit --approve."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def checked_config(config: dict) -> None:
    required = {
        "account",
        "region",
        "team_id",
        "stack_name",
        "memory_id",
        "artifact_bucket",
        "alarm_topic_arn",
        "collection_enabled",
        "log_groups",
    }
    if not isinstance(config, dict) or not required <= set(config) <= required | {
        "learning_enabled"
    }:
        raise ValueError("learning config must supply exactly the documented fields")
    for key, pattern in {
        "account": r"[0-9]{12}",
        "region": r"us-(east|west)-[12]",
        "team_id": r"[A-Za-z0-9_-]{1,64}",
        "stack_name": r"[A-Za-z][A-Za-z0-9-]{1,44}",
        "memory_id": r"[A-Za-z0-9_-]{1,128}",
        "artifact_bucket": r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]",
    }.items():
        if not isinstance(config[key], str) or not re.fullmatch(pattern, config[key]):
            raise ValueError(f"invalid learning config {key}")
    topic_prefix = f"arn:aws:sns:{config['region']}:{config['account']}:"
    if config["alarm_topic_arn"] != "" and (
        not isinstance(config["alarm_topic_arn"], str)
        or not re.fullmatch(
            re.escape(topic_prefix) + r"[A-Za-z0-9_-]{1,256}", config["alarm_topic_arn"]
        )
    ):
        raise ValueError("alarm topic must belong to expected account and region")
    groups = config["log_groups"]
    if (
        not isinstance(groups, list)
        or len(groups) != 5
        or len(set(groups)) != 5
        or any(
            not isinstance(group, str)
            or not re.fullmatch(r"/aws/bedrock-agentcore/runtimes/[A-Za-z0-9_-]+-DEFAULT", group)
            for group in groups
        )
    ):
        raise ValueError("supply exactly five distinct runtime log groups in seat order")
    if type(config["collection_enabled"]) is not bool:
        raise ValueError("collection_enabled must be a boolean")
    if type(config.get("learning_enabled", False)) is not bool:
        raise ValueError("learning_enabled must be a boolean")


def template(config: dict, code_key: str) -> dict:
    checked_config(config)
    region, account, team = (config[k] for k in ("region", "account", "team_id"))
    function = config["stack_name"] + "-collector"
    log_arn = f"arn:aws:logs:{region}:{account}:log-group:/aws/lambda/{function}:*"
    memory_arn = f"arn:aws:bedrock-agentcore:{region}:{account}:memory/{config['memory_id']}"

    def arn(name):
        return {"Fn::GetAtt": [name, "Arn"]}

    def ref(name):
        return {"Ref": name}

    resources = {}
    for name in ("Registry", "Outbox"):
        resources[name] = {
            "Type": "AWS::DynamoDB::Table",
            "DeletionPolicy": "Retain",
            "UpdateReplacePolicy": "Retain",
            "Properties": {
                "BillingMode": "PAY_PER_REQUEST",
                "AttributeDefinitions": [{"AttributeName": "pk", "AttributeType": "S"}],
                "KeySchema": [{"AttributeName": "pk", "KeyType": "HASH"}],
                "SSESpecification": {"SSEEnabled": True},
                "PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True},
            },
        }
    resources["Outbox"]["Properties"]["AttributeDefinitions"].extend(
        [
            {"AttributeName": "team", "AttributeType": "S"},
            {"AttributeName": "received", "AttributeType": "N"},
        ]
    )
    resources["Outbox"]["Properties"]["GlobalSecondaryIndexes"] = [
        {
            "IndexName": "TeamReceived",
            "KeySchema": [
                {"AttributeName": "team", "KeyType": "HASH"},
                {"AttributeName": "received", "KeyType": "RANGE"},
            ],
            "Projection": {"ProjectionType": "ALL"},
        }
    ]
    resources["Failures"] = {
        "Type": "AWS::SQS::Queue",
        "DeletionPolicy": "Retain",
        "UpdateReplacePolicy": "Retain",
        "Properties": {"MessageRetentionPeriod": 1_209_600, "SqsManagedSseEnabled": True},
    }
    resources["WorkerLogs"] = {
        "Type": "AWS::Logs::LogGroup",
        "Properties": {"LogGroupName": f"/aws/lambda/{function}", "RetentionInDays": 14},
    }
    resources["WorkerRole"] = {
        "Type": "AWS::IAM::Role",
        "Properties": {
            "AssumeRolePolicyDocument": {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "lambda.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            },
            "Policies": [
                {
                    "PolicyName": "ProvisionalObservations",
                    "PolicyDocument": {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                                "Resource": log_arn,
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:GetItem",
                                    "dynamodb:PutItem",
                                    "dynamodb:UpdateItem",
                                ],
                                "Resource": arn("Outbox"),
                            },
                            {
                                "Effect": "Allow",
                                "Action": "bedrock-agentcore:CreateEvent",
                                "Resource": memory_arn,
                            },
                            {
                                "Effect": "Allow",
                                "Action": "sqs:SendMessage",
                                "Resource": arn("Failures"),
                            },
                        ],
                    },
                }
            ],
        },
    }
    resources["Worker"] = {
        "Type": "AWS::Lambda::Function",
        "DependsOn": "WorkerLogs",
        "Properties": {
            "FunctionName": function,
            "Runtime": "python3.12",
            "Handler": "agent.learning_collector.handler",
            "Role": arn("WorkerRole"),
            "Code": {"S3Bucket": config["artifact_bucket"], "S3Key": code_key},
            "Timeout": 300,
            "MemorySize": 256,
            "ReservedConcurrentExecutions": 2,
            "Environment": {
                "Variables": {
                    "AFC_TEAM_ID": team,
                    "AFC_MEMORY_ID": config["memory_id"],
                    "AFC_OBSERVATION_TABLE": ref("Outbox"),
                    "AFC_LOG_GROUPS": json.dumps(config["log_groups"]),
                }
            },
        },
    }
    resources["Delivery"] = {
        "Type": "AWS::Lambda::EventInvokeConfig",
        "Properties": {
            "FunctionName": ref("Worker"),
            "Qualifier": "$LATEST",
            "MaximumRetryAttempts": 2,
            "MaximumEventAgeInSeconds": 3600,
            "DestinationConfig": {"OnFailure": {"Destination": arn("Failures")}},
        },
    }
    resources["LearnerRole"] = {
        "Type": "AWS::IAM::Role",
        "Properties": {
            "AssumeRolePolicyDocument": {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "lambda.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            },
            "Policies": [
                {
                    "PolicyName": "BoundedLearning",
                    "PolicyDocument": {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "logs:CreateLogStream",
                                    "logs:PutLogEvents",
                                ],
                                "Resource": (
                                    f"arn:aws:logs:{region}:{account}:log-group:/aws/lambda/"
                                    + config["stack_name"]
                                    + "-learner:*"
                                ),
                            },
                            {
                                "Effect": "Allow",
                                "Action": ["dynamodb:Query"],
                                "Resource": {
                                    "Fn::Join": ["", [arn("Outbox"), "/index/TeamReceived"]]
                                },
                            },
                            {
                                "Effect": "Allow",
                                "Action": [
                                    "dynamodb:GetItem",
                                    "dynamodb:PutItem",
                                    "dynamodb:UpdateItem",
                                    "dynamodb:ConditionCheckItem",
                                ],
                                "Resource": arn("Registry"),
                                "Condition": {
                                    "ForAllValues:StringLike": {
                                        "dynamodb:LeadingKeys": [f"TEAM#{team}#*"]
                                    }
                                },
                            },
                            {
                                "Effect": "Allow",
                                "Action": "bedrock-agentcore:ListEvents",
                                "Resource": memory_arn,
                            },
                            {
                                "Effect": "Allow",
                                "Action": "sqs:SendMessage",
                                "Resource": arn("Failures"),
                            },
                        ],
                    },
                }
            ],
        },
    }
    resources["LearnerLogs"] = {
        "Type": "AWS::Logs::LogGroup",
        "Properties": {
            "LogGroupName": f"/aws/lambda/{config['stack_name']}-learner",
            "RetentionInDays": 14,
        },
    }
    resources["Learner"] = {
        "Type": "AWS::Lambda::Function",
        "DependsOn": "LearnerLogs",
        "Properties": {
            "FunctionName": config["stack_name"] + "-learner",
            "Runtime": "python3.12",
            "Handler": "agent.learning_worker.handler",
            "Role": arn("LearnerRole"),
            "Code": {"S3Bucket": config["artifact_bucket"], "S3Key": code_key},
            "Timeout": 900,
            "MemorySize": 1024,
            "ReservedConcurrentExecutions": 1,
            "Environment": {
                "Variables": {
                    "AFC_TEAM_ID": team,
                    "AFC_MEMORY_ID": config["memory_id"],
                    "AFC_OBSERVATION_TABLE": ref("Outbox"),
                    "AFC_PROFILE_TABLE": ref("Registry"),
                    "AFC_LEARNING_ENABLED": "1" if config.get("learning_enabled", False) else "0",
                }
            },
        },
    }
    resources["LearningDelivery"] = {
        "Type": "AWS::Lambda::EventInvokeConfig",
        "Properties": {
            "FunctionName": ref("Learner"),
            "Qualifier": "$LATEST",
            "MaximumRetryAttempts": 0,
            "MaximumEventAgeInSeconds": 3600,
            "DestinationConfig": {"OnFailure": {"Destination": arn("Failures")}},
        },
    }
    resources["LearningSchedule"] = {
        "Type": "AWS::Events::Rule",
        "DependsOn": "LearningDelivery",
        "Properties": {
            "ScheduleExpression": "rate(15 minutes)",
            "State": "ENABLED" if config.get("learning_enabled", False) else "DISABLED",
            "Targets": [
                {
                    "Id": "BoundedLearner",
                    "Arn": arn("Learner"),
                    "RetryPolicy": {"MaximumRetryAttempts": 0, "MaximumEventAgeInSeconds": 3600},
                }
            ],
        },
    }
    resources["LearningInvokePermission"] = {
        "Type": "AWS::Lambda::Permission",
        "Properties": {
            "FunctionName": ref("Learner"),
            "Action": "lambda:InvokeFunction",
            "Principal": "events.amazonaws.com",
            "SourceArn": arn("LearningSchedule"),
        },
    }
    topic = config["alarm_topic_arn"]
    if not topic:
        resources["AlarmTopic"] = {
            "Type": "AWS::SNS::Topic",
            "Properties": {"DisplayName": "final_agent learning alarms"},
        }
        topic = ref("AlarmTopic")
    for seat, group in enumerate(config["log_groups"]):
        permission = f"LogPermission{seat}"
        resources[permission] = {
            "Type": "AWS::Lambda::Permission",
            "Condition": "Collect",
            "Properties": {
                "FunctionName": ref("Worker"),
                "Action": "lambda:InvokeFunction",
                "Principal": f"logs.{region}.amazonaws.com",
                "SourceAccount": account,
                "SourceArn": f"arn:aws:logs:{region}:{account}:log-group:{group}:*",
            },
        }
        resources[f"Subscription{seat}"] = {
            "Type": "AWS::Logs::SubscriptionFilter",
            "Condition": "Collect",
            "DependsOn": [permission, "Delivery"],
            "Properties": {
                "LogGroupName": group,
                "DestinationArn": arn("Worker"),
                "FilterPattern": (
                    '{ ($.type = "decision") || ($.type = "error")'
                    ' || ($.type = "profile_selection_error") }'
                ),
            },
        }
    # These permissions are created but deliberately not attached to live runtime roles.
    resources["PlayerPolicy"] = {
        "Type": "AWS::IAM::ManagedPolicy",
        "Properties": {
            "Description": "Opt-in match profile reads and transactional pin creation only",
            "PolicyDocument": {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": "dynamodb:GetItem",
                        "Resource": arn("Registry"),
                        "Condition": {
                            "ForAllValues:StringLike": {"dynamodb:LeadingKeys": [f"TEAM#{team}#*"]}
                        },
                    },
                    {
                        "Effect": "Allow",
                        "Action": "dynamodb:PutItem",
                        "Resource": arn("Registry"),
                        "Condition": {
                            "ForAllValues:StringLike": {
                                "dynamodb:LeadingKeys": [f"TEAM#{team}#MATCH#*"]
                            },
                            "StringEquals": {"dynamodb:EnclosingOperation": "TransactWriteItems"},
                        },
                    },
                    {
                        "Effect": "Allow",
                        "Action": "dynamodb:ConditionCheckItem",
                        "Resource": arn("Registry"),
                        "Condition": {
                            "ForAllValues:StringLike": {
                                "dynamodb:LeadingKeys": [f"TEAM#{team}#ACTIVE"]
                            },
                            "StringEquals": {"dynamodb:EnclosingOperation": "TransactWriteItems"},
                        },
                    },
                ],
            },
        },
    }
    for name, namespace, metric, dimensions in (
        (
            "WorkerErrors",
            "AWS/Lambda",
            "Errors",
            [{"Name": "FunctionName", "Value": ref("Worker")}],
        ),
        (
            "WorkerThrottles",
            "AWS/Lambda",
            "Throttles",
            [{"Name": "FunctionName", "Value": ref("Worker")}],
        ),
        (
            "DeadLetters",
            "AWS/SQS",
            "ApproximateNumberOfMessagesVisible",
            [{"Name": "QueueName", "Value": {"Fn::GetAtt": ["Failures", "QueueName"]}}],
        ),
        (
            "DestinationFailures",
            "AWS/Lambda",
            "DestinationDeliveryFailures",
            [{"Name": "FunctionName", "Value": ref("Worker")}],
        ),
    ):
        resources[name] = {
            "Type": "AWS::CloudWatch::Alarm",
            "Properties": {
                "Namespace": namespace,
                "MetricName": metric,
                "Dimensions": dimensions,
                "Statistic": "Sum",
                "Period": 60,
                "EvaluationPeriods": 1,
                "Threshold": 0,
                "ComparisonOperator": "GreaterThanThreshold",
                "TreatMissingData": "notBreaching",
                "AlarmActions": [topic],
            },
        }
    resources["LearnerErrors"] = {
        "Type": "AWS::CloudWatch::Alarm",
        "Properties": {
            "Namespace": "AWS/Lambda",
            "MetricName": "Errors",
            "Dimensions": [{"Name": "FunctionName", "Value": ref("Learner")}],
            "Statistic": "Sum",
            "Period": 60,
            "EvaluationPeriods": 1,
            "Threshold": 0,
            "ComparisonOperator": "GreaterThanThreshold",
            "TreatMissingData": "notBreaching",
            "AlarmActions": [topic],
        },
    }
    return {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Description": "final_agent provisional shared Memory and immutable match profiles",
        "Parameters": {
            "CollectionEnabled": {
                "Type": "String",
                "AllowedValues": ["true", "false"],
                "Default": str(config["collection_enabled"]).lower(),
            }
        },
        "Conditions": {"Collect": {"Fn::Equals": [{"Ref": "CollectionEnabled"}, "true"]}},
        "Resources": resources,
        "Outputs": {
            "RegistryTable": {
                "Value": ref("Registry"),
                "Export": {"Name": config["stack_name"] + "-RegistryTable"},
            },
            "ObservationTable": {"Value": ref("Outbox")},
            "Collector": {"Value": ref("Worker")},
            "FailureQueue": {"Value": ref("Failures")},
            "PlayerPolicyArn": {
                "Value": ref("PlayerPolicy"),
                "Export": {"Name": config["stack_name"] + "-PlayerPolicyArn"},
            },
            "Learner": {"Value": ref("Learner")},
            "AlarmTopicArn": {"Value": topic},
        },
    }


def package(output: Path) -> str:
    """Vendor the installed pure-Python AWS SDK, not the unknown Lambda SDK version."""
    version = importlib.metadata.version("boto3")
    if tuple(map(int, version.split(".")[:3])) < (1, 43, 115):
        raise ValueError("install final_agent[learning] before packaging")
    files: dict[str, Path] = {}
    for name in (
        "__init__.py",
        "learning_collector.py",
        "learning_observations.py",
        "profile_registry.py",
        "model.py",
        "profile.py",
        "protocol.py",
        "learning_source.py",
        "learning_policy.py",
        "learning_worker.py",
        "learning_evaluation.py",
        "tactics.py",
        "coach.py",
        "archetypes.py",
    ):
        files["agent/" + name] = ROOT / "agent" / name
    simulator = ROOT.parent / "local_simulator" / "src" / "football_lab"
    if not simulator.is_dir():
        raise ValueError("offline worker packaging requires the repository local_simulator source")
    for source in simulator.rglob("*.py"):
        files["football_lab/" + source.relative_to(simulator).as_posix()] = source
    for dependency in (
        "boto3",
        "botocore",
        "jmespath",
        "s3transfer",
        "python-dateutil",
        "six",
        "urllib3",
    ):
        distribution = importlib.metadata.distribution(dependency)
        for path in distribution.files or ():
            if ".." not in path.parts and "__pycache__" not in path.parts and path.suffix != ".pyc":
                files[str(path)] = Path(distribution.locate_file(path))
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, path in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, path.read_bytes())
    return hashlib.sha256(output.read_bytes()).hexdigest()


def identity(config: dict):
    import boto3

    session = boto3.Session(region_name=config["region"])
    actual = session.client("sts").get_caller_identity()["Account"]
    if actual != config["account"]:
        raise ValueError("AWS account differs from learning config")
    return session


def owned_stack(client, config: dict) -> dict | None:
    from botocore.exceptions import ClientError

    try:
        stack = client.describe_stacks(StackName=config["stack_name"])["Stacks"][0]
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ValidationError" and (
            "does not exist" in exc.response["Error"]["Message"]
        ):
            return None
        raise
    tags = {tag["Key"]: tag["Value"] for tag in stack.get("Tags", [])}
    if tags.get("afc:team") != config["team_id"] or tags.get("afc:component") != "learning":
        raise ValueError("refusing to modify an unowned CloudFormation stack")
    return stack


def apply(config: dict) -> None:
    from botocore.exceptions import ClientError

    checked_config(config)
    session = identity(config)
    cloudformation = session.client("cloudformation")
    stack = owned_stack(cloudformation, config)
    with tempfile.TemporaryDirectory() as directory:
        archive = Path(directory) / "learning.zip"
        digest = package(archive)
        key = f"final-agent-learning/{config['team_id']}/{digest}.zip"
        # Content-derived keys are never reused for different code.
        session.client("s3").put_object(
            Bucket=config["artifact_bucket"],
            Key=key,
            Body=archive.read_bytes(),
            ExpectedBucketOwner=config["account"],
            ServerSideEncryption="AES256",
        )
    request = {
        "StackName": config["stack_name"],
        "TemplateBody": json.dumps(template(config, key)),
        "Capabilities": ["CAPABILITY_IAM"],
        "Parameters": [
            {
                "ParameterKey": "CollectionEnabled",
                "ParameterValue": str(config["collection_enabled"]).lower(),
            }
        ],
        "Tags": [
            {"Key": "afc:team", "Value": config["team_id"]},
            {"Key": "afc:component", "Value": "learning"},
        ],
    }
    if stack is None:
        cloudformation.create_stack(**request)
        waiter = "stack_create_complete"
    else:
        try:
            cloudformation.update_stack(**request)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ValidationError" and (
                exc.response["Error"]["Message"] == "No updates are to be performed."
            ):
                print("learning stack already matches configuration")
                return
            raise
        waiter = "stack_update_complete"
    cloudformation.get_waiter(waiter).wait(StackName=config["stack_name"])
    print(json.dumps(owned_stack(cloudformation, config)["Outputs"], indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "package", "apply", "bootstrap"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--profile", type=Path, default=ROOT / "profiles/recommended.json")
    parser.add_argument("--approve", action="store_true")
    args = parser.parse_args()
    if args.command == "package":
        if args.output is None:
            parser.error("package requires --output")
        print(package(args.output))
        return
    if args.config is None:
        parser.error("--config is required")
    config = json.loads(args.config.read_text())
    checked_config(config)
    if args.command == "plan":
        print(json.dumps(template(config, "PACKAGED_SHA256.zip"), indent=2))
        return
    if not args.approve:
        parser.error("AWS writes require --approve")
    if args.command == "apply":
        apply(config)
        return
    sys.path.insert(0, str(ROOT))
    from agent.profile import load_profile
    from agent.profile_registry import DynamoProfileRegistry

    session = identity(config)
    stack = owned_stack(session.client("cloudformation"), config)
    if stack is None:
        raise ValueError("apply the owned learning stack before bootstrap")
    outputs = {item["OutputKey"]: item["OutputValue"] for item in stack["Outputs"]}
    digest = DynamoProfileRegistry(
        session.client("dynamodb"), outputs["RegistryTable"], config["team_id"]
    ).bootstrap(load_profile(args.profile))
    print(json.dumps({"bootstrapped_digest": digest, "table": outputs["RegistryTable"]}))


if __name__ == "__main__":
    main()
