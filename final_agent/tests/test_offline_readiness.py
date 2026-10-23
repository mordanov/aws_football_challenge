"""Operator-path regressions that permissive service fakes did not catch."""

import ast
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from botocore.exceptions import ClientError, ReadTimeoutError
from botocore.session import get_session
from botocore.validate import validate_parameters

from agent.advisor.offline.__main__ import Services, main
from agent.advisor.offline.bedrock import GroundedAdvisor
from agent.advisor.offline.contracts import OfflineServiceError, ScoutingContext
from agent.advisor.offline.evaluation import AgentCoreEvaluator
from agent.model import Profile
from tests.offline_support import (
    FakeAdvisor,
    FakeBedrockClient,
    FakeEvaluator,
    FakeMemory,
    make_config,
    make_packet,
)


class SdkBoundaryTest(unittest.TestCase):
    def test_structured_advice_request_is_sdk_serializable(self):
        client = FakeBedrockClient()
        advisor = GroundedAdvisor(client, make_config(structured_outputs=True), {})
        advisor.review_evidence(
            make_packet(),
            Profile(),
            ScoutingContext(notes=(), extracted_text=(), excluded=()),
            None,
        )
        model = get_session().get_service_model("bedrock-runtime")
        validate_parameters(client.calls[0], model.operation_model("Converse").input_shape)
        schema = json.loads(
            client.calls[0]["outputConfig"]["textFormat"]["structure"]["jsonSchema"]["schema"]
        )
        for name, values in (
            ("settings", Profile().to_dict()["settings"]),
            ("style", Profile().to_dict()["style"]),
        ):
            section = schema["properties"][name]
            self.assertIs(section.get("additionalProperties"), False)
            self.assertEqual(set(section["required"]), set(values))

    def test_evaluator_reads_sdk_value_and_preserves_trace(self):
        class Client:
            def evaluate(self, **kwargs):
                model = get_session().get_service_model("bedrock-agentcore")
                validate_parameters(kwargs, model.operation_model("Evaluate").input_shape)
                return {
                    "evaluationResults": [
                        {
                            "evaluatorId": "evaluator-001",
                            "value": 3.5,
                            "context": {
                                "spanContext": {"sessionId": "run-1", "traceId": "trace-1"}
                            },
                            "explanation": "grounded in the actual claims",
                        }
                    ]
                }

        report = AgentCoreEvaluator(Client(), make_config()).evaluate(
            [
                {
                    "traceId": "trace-1",
                    "spanId": "span-1",
                    "name": "gen_ai.inference",
                    "attributes": {
                        "gen_ai.operation.name": "chat",
                        "gen_ai.task.input": "SHOOT=3 is an attempted command",
                        "gen_ai.task.output": "No confirmed goal attribution",
                    },
                }
            ]
        )
        self.assertEqual(report.status, "ok")
        self.assertEqual(report.scores, {"faithfulness": 3.5})
        self.assertEqual(report.trace_id, "trace-1")

    def test_evaluator_service_result_error_is_not_a_partial_success(self):
        class Client:
            def evaluate(self, **kwargs):
                return {
                    "evaluationResults": [
                        {
                            "evaluatorId": "evaluator-001",
                            "errorCode": "ThrottlingException",
                            "errorMessage": "not scored",
                        }
                    ]
                }

        with self.assertRaises(OfflineServiceError):
            AgentCoreEvaluator(Client(), make_config()).evaluate(
                [
                    {
                        "traceId": "t",
                        "spanId": "s",
                        "name": "gen_ai.inference",
                        "attributes": {
                            "gen_ai.operation.name": "chat",
                            "gen_ai.task.input": "evidence",
                            "gen_ai.task.output": "advice",
                        },
                    }
                ]
            )

    def test_advisor_rejects_input_and_output_budget_overruns(self):
        packet = make_packet()
        scouting = ScoutingContext(notes=(), extracted_text=(), excluded=())
        for limits, expected_calls in (({"max_input_tokens": 1}, 0), ({"max_output_bytes": 1}, 1)):
            client = FakeBedrockClient()
            config = make_config().model_copy(update=limits)
            with self.subTest(limits=limits), self.assertRaises(OfflineServiceError):
                GroundedAdvisor(client, config, {}).review_evidence(
                    packet, Profile(), scouting, None
                )
            self.assertEqual(len(client.calls), expected_calls)

    def test_evidence_claim_references_are_unique_and_resolvable(self):
        from agent.advisor.offline.contracts import EvidencePacket

        packet = make_packet()
        for changes in (
            {"claims": [packet.claims[0].model_dump()] * 2},
            {"source_digests": {}},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                EvidencePacket.model_validate({**packet.model_dump(), **changes})

    def test_failed_evaluation_cannot_produce_a_proposal(self):
        from tests.offline_support import make_workflow_case

        case = make_workflow_case()
        good_report = case.evaluator.evaluate([])

        class Evaluator:
            def evaluate(self, spans):
                return good_report.model_copy(update={"status": "failed"})

        case.evaluator = Evaluator()
        self.assertEqual(case.run().status, "failed")


class OperatorPathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.packet = make_packet()
        self.write("config.json", make_config().model_dump(mode="json"))
        self.write("evidence.json", self.packet.model_dump(mode="json"))
        self.write("profile.json", Profile().to_dict())

    def write(self, name, data):
        path = self.root / name
        path.write_text(json.dumps(data))
        return str(path)

    def cli(self, *args, services=None):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(list(args), services=services)

    def test_prepare_retains_telemetry_report_contradiction(self):
        case = json.loads(
            (Path(__file__).parent / "fixtures/offline/report3_case.json").read_text()
        )
        manifest = self.write("manifest.json", case["manifest"])
        report = self.write("report.json", case["report"])
        coaches = self.write("coaches.json", {"claims": []})
        code = self.cli(
            "prepare",
            "--manifest",
            manifest,
            "--report",
            report,
            "--coach-claims",
            coaches,
            "--profile",
            str(self.root / "profile.json"),
            "--team",
            "aleksalotl",
            "--expires-at",
            "2027-10-23T00:00:00Z",
            "--output",
            str(self.root / "prepared.json"),
        )
        self.assertEqual(code, 0)
        claims = json.loads((self.root / "prepared.json").read_text())["claims"]
        shots = [c for c in claims if "SHOOT" in c["text"]]
        self.assertEqual({c["kind"] for c in shots}, {"observed", "report_claim"})

    def test_real_advisor_factory_imports_archetypes_without_network(self):
        from agent.advisor.offline.__main__ import _real_advisor

        with patch("boto3.client", return_value=FakeBedrockClient()):
            self.assertIsInstance(_real_advisor(make_config()), GroundedAdvisor)

    def test_generate_analysis_creates_bound_script_without_executing(self):
        script_code = 'def analyze(evidence):\n    return {"claims": len(evidence["claims"])}\n'
        client = FakeBedrockClient(response_json=json.dumps({"code": script_code}))
        with patch("boto3.client", return_value=client):
            code = self.cli(
                "generate-analysis",
                "--config",
                str(self.root / "config.json"),
                "--evidence",
                str(self.root / "evidence.json"),
                "--instruction",
                "Summarize counts",
                "--output",
                str(self.root / "script.json"),
            )
        self.assertEqual(code, 0)
        script = json.loads((self.root / "script.json").read_text())
        self.assertEqual(script["evidence_digest"], self.packet.digest)
        self.assertTrue(script["code"].startswith(script_code))
        tree = ast.parse(script["code"])
        self.assertEqual(tree.body[0].name, "analyze")
        self.assertIn('open("evidence.json", encoding="utf-8")', script["code"])
        self.assertIn("_afc_result = analyze(_afc_evidence)", script["code"])
        self.assertIn("allow_nan=False", script["code"])
        self.assertIn("usage", script)

    def test_count_analysis_creates_trusted_script_without_aws(self):
        output = self.root / "counts-script.json"
        with patch("boto3.client", side_effect=AssertionError("AWS forbidden")):
            exit_code = self.cli(
                "count-analysis",
                "--evidence",
                str(self.root / "evidence.json"),
                "--output",
                str(output),
            )
        self.assertEqual(exit_code, 0)
        artifact = json.loads(output.read_text())
        self.assertEqual(artifact["evidence_digest"], self.packet.digest)
        self.assertEqual(artifact["origin"], "deterministic_shooting_counts_v1")
        self.assertIsNone(artifact["usage"])

    def test_advisor_gets_deterministic_counts_without_optional_analysis(self):
        client = FakeBedrockClient()
        advisor = GroundedAdvisor(client, make_config(), {})
        advisor.review_evidence(
            self.packet, Profile(), ScoutingContext(notes=(), extracted_text=(), excluded=()), None
        )
        payload = json.loads(client.calls[0]["messages"][0]["content"][0]["text"])
        self.assertEqual(payload["shooting_counts"]["observed"]["count"], 3)
        self.assertEqual(payload["shooting_counts"]["report_claim"]["count"], 0)

    def test_invalid_counts_cannot_create_script_or_reach_model(self):
        data = self.packet.model_dump(mode="json")
        data["claims"][0]["text"] = "SHOOT command issued invalid times"
        self.write("invalid-counts.json", data)
        output = self.root / "invalid-script.json"
        exit_code = self.cli(
            "count-analysis",
            "--evidence",
            str(self.root / "invalid-counts.json"),
            "--output",
            str(output),
        )
        self.assertEqual(exit_code, 2)
        self.assertFalse(output.exists())
        from agent.advisor.offline.contracts import EvidencePacket

        client = FakeBedrockClient(side_effect=AssertionError("Model must not be called"))
        advisor = GroundedAdvisor(client, make_config(), {})
        with self.assertRaises(OfflineServiceError) as raised:
            advisor.review_evidence(
                EvidencePacket.model_validate(data),
                Profile(),
                ScoutingContext(notes=(), extracted_text=(), excluded=()),
                None,
            )
        self.assertEqual(raised.exception.error_code, "MALFORMED_SHOOT_COUNT")
        self.assertEqual(client.calls, [])

    def test_advisor_accepts_only_complete_fence_and_keeps_failed_usage_in_run(self):
        from tests.offline_support import _make_default_advice_json

        for suffix, expected_status in (("", "proposal"), ("\nextra prose", "failed")):
            with self.subTest(suffix=suffix):
                client = FakeBedrockClient(
                    response_json="```json\n" + _make_default_advice_json() + "\n```" + suffix
                )
                services = Services(
                    scouts=FakeMemory(),
                    advisor=GroundedAdvisor(client, make_config(), {}),
                    evaluator=FakeEvaluator(),
                )
                output = self.root / "fenced-run.json"
                exit_code = self.cli(
                    "run",
                    "--config",
                    str(self.root / "config.json"),
                    "--evidence",
                    str(self.root / "evidence.json"),
                    "--profile",
                    str(self.root / "profile.json"),
                    "--output",
                    str(output),
                    services=services,
                )
                result = json.loads(output.read_text())
                self.assertEqual(result["status"], expected_status)
                self.assertEqual(exit_code, 0 if expected_status == "proposal" else 2)
                self.assertEqual(result["stage_receipts"]["advise"]["input_tokens"], 100)
                if expected_status == "failed":
                    self.assertIsNone(result["candidate_profile"])

    def test_model_and_evaluation_clients_use_bounded_standard_retries(self):
        from agent.advisor.offline.__main__ import _aws_client

        for service in ("bedrock-runtime", "bedrock-agentcore"):
            with patch("boto3.client") as factory:
                _aws_client(make_config(), service, retry_reads=True)
            retries = factory.call_args.kwargs["config"].retries
            self.assertEqual(retries, {"mode": "standard", "total_max_attempts": 3})
        with patch("boto3.client") as factory:
            _aws_client(make_config(), "bedrock-agentcore")
        self.assertEqual(factory.call_args.kwargs["config"].retries, {"max_attempts": 0})

    def test_generate_analysis_accepts_complete_json_fence_without_execution(self):
        script_code = 'def analyze(evidence):\n    return {"claims": len(evidence["claims"])}\n'
        text = "```json\n" + json.dumps({"code": script_code}) + "\n```"
        client = FakeBedrockClient(response_json=text)
        advisor = GroundedAdvisor(client, make_config(), {})
        artifact = advisor.generate_analysis(self.packet, "Summarize counts")
        self.assertTrue(artifact["code"].startswith(script_code))
        self.assertEqual(artifact["evidence_digest"], self.packet.digest)
        self.assertEqual(artifact["usage"]["input_tokens"], 100)

    def test_generation_rejects_hardcoded_summary_and_keeps_usage(self):
        client = FakeBedrockClient(
            response_json=json.dumps({"code": 'import json\nprint(json.dumps({"count": 3}))'})
        )
        advisor = GroundedAdvisor(client, make_config(), {})
        with self.assertRaises(OfflineServiceError) as raised:
            advisor.generate_analysis(self.packet, "Summarize counts")
        self.assertEqual(raised.exception.error_code, "INVALID_ANALYSIS_FUNCTION")
        self.assertEqual(advisor.last_generation_diagnostics["usage"]["input_tokens"], 100)

    def test_generation_rejects_executable_top_level_and_invalid_function_contract(self):
        for code in (
            'def analyze(evidence):\n    return {}\nprint("unsafe")',
            "@dangerous()\ndef analyze(evidence):\n    return {}",
            "def analyze(evidence=load()):\n    return {}",
            "def analyze(other):\n    return {}",
            "def analyze(evidence) -> dangerous():\n    return {}",
            "async def analyze(evidence):\n    return {}",
            "def analyze(evidence):\n invalid python !",
        ):
            with self.subTest(code=code):
                advisor = GroundedAdvisor(
                    FakeBedrockClient(response_json=json.dumps({"code": code})),
                    make_config(),
                    {},
                )
                with self.assertRaises(OfflineServiceError) as raised:
                    advisor.generate_analysis(self.packet, "Summarize counts")
                self.assertEqual(raised.exception.error_code, "INVALID_ANALYSIS_FUNCTION")
                self.assertEqual(advisor.last_generation_diagnostics["usage"]["output_tokens"], 50)

    def test_generation_sends_schema_not_packet_values(self):
        client = FakeBedrockClient(response_json='{"code": ""}')
        advisor = GroundedAdvisor(client, make_config(), {})
        with self.assertRaises(OfflineServiceError):
            advisor.generate_analysis(self.packet, "Summarize counts")
        prompt = json.dumps(client.calls)
        for claim in self.packet.claims:
            self.assertNotIn(claim.text, prompt)
        self.assertIn("evidence_schema", prompt)
        self.assertIn("source_ids", prompt)

    def test_generation_failure_writes_bounded_redacted_diagnostics_and_usage(self):
        cases = (
            ('```json\n{"code": "print(42)"}\n```\nsecret-output', "INVALID_JSON"),
            ('{"code": "", "secret": "secret-output"}', "INVALID_SCRIPT_SHAPE"),
            ('{"code": ""}', "EMPTY_CODE"),
            ("secret-output" * 10000, "OUTPUT_LIMIT"),
        )
        for text, reason in cases:
            with self.subTest(reason=reason):
                output = self.root / "failed-script.json"
                client = FakeBedrockClient(response_json=text)
                with patch("boto3.client", return_value=client):
                    code = self.cli(
                        "generate-analysis",
                        "--config",
                        str(self.root / "config.json"),
                        "--evidence",
                        str(self.root / "evidence.json"),
                        "--instruction",
                        "Summarize counts",
                        "--output",
                        str(output),
                    )
                self.assertEqual(code, 2)
                self.assertFalse(output.exists())
                diagnostic_text = Path(str(output) + ".diagnostics.json").read_text()
                diagnostic = json.loads(diagnostic_text)
                self.assertEqual(diagnostic["reason"], reason)
                self.assertEqual(diagnostic["usage"]["input_tokens"], 100)
                self.assertEqual(diagnostic["usage"]["output_tokens"], 50)
                self.assertEqual(diagnostic["response_bytes"], len(text.encode()))
                self.assertNotIn("secret-output", diagnostic_text)
                self.assertLess(len(diagnostic_text.encode()), 4096)

    def test_generation_truncation_keeps_usage_without_a_script(self):
        client = FakeBedrockClient(
            response_json='{"code": "secret-output', stop_reason="max_tokens"
        )
        advisor = GroundedAdvisor(client, make_config(), {})
        with self.assertRaises(OfflineServiceError):
            advisor.generate_analysis(self.packet, "Summarize counts")
        diagnostic = advisor.last_generation_diagnostics
        self.assertEqual(diagnostic["reason"], "INCOMPLETE_RESPONSE")
        self.assertEqual(diagnostic["usage"]["output_tokens"], 50)
        self.assertNotIn("secret-output", json.dumps(diagnostic))

    def test_generation_service_failure_has_no_invented_usage(self):
        client = FakeBedrockClient(side_effect=RuntimeError("secret-credential"))
        advisor = GroundedAdvisor(client, make_config(), {})
        with self.assertRaises(OfflineServiceError) as raised:
            advisor.generate_analysis(self.packet, "Summarize counts")
        self.assertEqual(advisor.last_generation_diagnostics["reason"], "SERVICE_ERROR")
        self.assertIsNone(advisor.last_generation_diagnostics["usage"])
        self.assertNotIn("secret-credential", str(raised.exception))

    def test_generation_failure_persists_only_safe_provider_metadata(self):
        for provider_code in ("ExpiredTokenException", "ThrottlingException", "secret-token"):
            with self.subTest(provider_code=provider_code):
                error = ClientError(
                    {
                        "Error": {"Code": provider_code, "Message": "secret-message"},
                        "ResponseMetadata": {
                            "HTTPStatusCode": 403,
                            "RequestId": "request-123",
                            "HTTPHeaders": {"authorization": "secret-header"},
                        },
                    },
                    "Converse",
                )
                client = FakeBedrockClient(side_effect=error)
                output = self.root / "service-failure.json"
                with patch("boto3.client", return_value=client):
                    exit_code = self.cli(
                        "generate-analysis",
                        "--config",
                        str(self.root / "config.json"),
                        "--evidence",
                        str(self.root / "evidence.json"),
                        "--instruction",
                        "Summarize counts",
                        "--output",
                        str(output),
                    )
                self.assertEqual(exit_code, 2)
                self.assertFalse(output.exists())
                text = Path(str(output) + ".diagnostics.json").read_text()
                diagnostic = json.loads(text)
                self.assertEqual(
                    diagnostic["provider_error"],
                    {
                        "code": provider_code if provider_code != "secret-token" else "UNKNOWN",
                        "http_status": 403,
                        "request_id": "request-123",
                    },
                )
                self.assertIsNone(diagnostic["usage"])
                self.assertNotIn("secret", text)
                self.assertLess(len(text), 4096)

    def test_generation_transport_failure_redacts_endpoint(self):
        client = FakeBedrockClient(
            side_effect=ReadTimeoutError(endpoint_url="https://secret-endpoint")
        )
        advisor = GroundedAdvisor(client, make_config(), {})
        with self.assertRaises(OfflineServiceError):
            advisor.generate_analysis(self.packet, "Summarize counts")
        diagnostic = advisor.last_generation_diagnostics
        self.assertEqual(diagnostic["provider_error"]["code"], "ReadTimeoutError")
        self.assertNotIn("secret", json.dumps(diagnostic))

    def test_run_preserves_grounding_for_low_score_reevaluation(self):
        services = Services(scouts=FakeMemory(), advisor=FakeAdvisor(), evaluator=FakeEvaluator())
        code = self.cli(
            "run",
            "--config",
            str(self.root / "config.json"),
            "--evidence",
            str(self.root / "evidence.json"),
            "--profile",
            str(self.root / "profile.json"),
            "--output",
            str(self.root / "run.json"),
            services=services,
        )
        self.assertEqual(code, 0)
        run = json.loads((self.root / "run.json").read_text())
        self.assertIn(self.packet.claims[0].text, json.dumps(run.get("evaluation_spans", [])))
        span = run["evaluation_spans"][0]
        self.assertEqual(span["attributes"]["gen_ai.operation.name"], "invoke_agent")
        self.assertTrue(span["scope"]["name"].startswith("opentelemetry.instrumentation."))
        services.evaluator = FakeEvaluator(score=0)
        code = self.cli(
            "evaluate",
            "--config",
            str(self.root / "config.json"),
            "--run",
            str(self.root / "run.json"),
            "--output",
            str(self.root / "rescored.json"),
            services=services,
        )
        self.assertEqual(code, 3)
        rescored = json.loads((self.root / "rescored.json").read_text())
        self.assertEqual(rescored["status"], "rejected")
        self.assertIsNone(rescored["candidate_profile"])

    def test_run_uses_only_analysis_bound_to_its_evidence(self):
        from agent.advisor.offline.contracts import AnalysisRequest
        from tests.offline_support import FakeInterpreter

        request = AnalysisRequest(
            code="print('{}')",
            data_json=json.dumps(self.packet.model_dump(mode="json"), sort_keys=True),
            timeout_seconds=30,
        )
        analysis = FakeInterpreter().execute(request)
        self.write("analysis.json", analysis.model_dump(mode="json"))

        class Advisor(FakeAdvisor):
            def review_evidence(inner_self, packet, current, scouting, result):
                self.assertIsNotNone(result)
                self.assertEqual(result.data_digest, request.data_digest)
                return super().review_evidence(packet, current, scouting, result)

        code = self.cli(
            "run",
            "--config",
            str(self.root / "config.json"),
            "--evidence",
            str(self.root / "evidence.json"),
            "--profile",
            str(self.root / "profile.json"),
            "--analysis",
            str(self.root / "analysis.json"),
            "--output",
            str(self.root / "run.json"),
            services=Services(scouts=FakeMemory(), advisor=Advisor(), evaluator=FakeEvaluator()),
        )
        self.assertEqual(code, 0)
        self.write("analysis.json", {**analysis.model_dump(mode="json"), "data_digest": "0" * 64})
        code = self.cli(
            "run",
            "--config",
            str(self.root / "config.json"),
            "--evidence",
            str(self.root / "evidence.json"),
            "--profile",
            str(self.root / "profile.json"),
            "--analysis",
            str(self.root / "analysis.json"),
            "--output",
            str(self.root / "invalid-run.json"),
            services=Services(scouts=FakeMemory(), advisor=Advisor(), evaluator=FakeEvaluator()),
        )
        self.assertEqual(code, 2)

    def test_evaluator_outage_recovery_does_not_repeat_paid_advice(self):
        services = Services(
            scouts=FakeMemory(),
            advisor=FakeAdvisor(),
            evaluator=FakeEvaluator(
                side_effect=OfflineServiceError(
                    "unavailable",
                    service="evaluator",
                    stage="evaluate",
                )
            ),
        )
        code = self.cli(
            "run",
            "--config",
            str(self.root / "config.json"),
            "--evidence",
            str(self.root / "evidence.json"),
            "--profile",
            str(self.root / "profile.json"),
            "--output",
            str(self.root / "run.json"),
            services=services,
        )
        self.assertEqual(code, 2)
        run = json.loads((self.root / "run.json").read_text())
        self.assertIsNone(run["candidate_profile"])
        services.evaluator = FakeEvaluator(score=4)
        code = self.cli(
            "evaluate",
            "--config",
            str(self.root / "config.json"),
            "--run",
            str(self.root / "run.json"),
            "--output",
            str(self.root / "recovered.json"),
            services=services,
        )
        self.assertEqual(code, 0)
        recovered = json.loads((self.root / "recovered.json").read_text())
        self.assertEqual(recovered["status"], "proposal")
        self.assertIsNotNone(recovered["candidate_profile"])
