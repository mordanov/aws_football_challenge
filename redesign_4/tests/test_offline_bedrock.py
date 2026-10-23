"""Tests for GroundedAdvisor: Converse wire protocol, output validation, usage capture."""

from __future__ import annotations

import importlib
import json
import unittest
from dataclasses import asdict

HAS_LANGCHAIN = importlib.util.find_spec("langchain_core") is not None


def _valid_advice_json(evidence_ids: tuple[str, ...] = ("claim-shoot-observed",)) -> str:
    from agent.model import DEFAULT_SETTINGS, DEFAULT_STYLE

    return json.dumps(
        {
            "settings": asdict(DEFAULT_SETTINGS),
            "style": asdict(DEFAULT_STYLE),
            "rationale": "maintain baseline; evidence insufficient for a specific change",
            "evidence_ids": list(evidence_ids),
            "uncertainties": ["SHOOT count contradicts between manifest and report"],
            "counter_tests": ["verify shot range if opponent increases press intensity"],
        }
    )


@unittest.skipUnless(HAS_LANGCHAIN, "install the llm extra")
class GroundedAdvisorTest(unittest.TestCase):
    def setUp(self) -> None:
        from agent.advisor.offline.bedrock import GroundedAdvisor  # noqa: PLC0415
        from agent.model import Profile
        from tests.offline_support import FakeBedrockClient, make_config, make_packet

        self.GroundedAdvisor = GroundedAdvisor
        self.FakeBedrockClient = FakeBedrockClient
        self.make_config = make_config
        self.packet = make_packet()
        self.current = Profile()

    def _make_advisor(self, client=None, *, structured_outputs: bool = False):
        from agent.model import Profile

        client = client or self.FakeBedrockClient()
        config = self.make_config(structured_outputs=structured_outputs)
        return self.GroundedAdvisor(client, config, {"baseline": Profile()})

    def _empty_scouting(self):
        from agent.advisor.offline.contracts import ScoutingContext

        return ScoutingContext(notes=(), extracted_text=(), excluded=())

    def test_happy_path_returns_advice_and_profile(self):
        advisor = self._make_advisor()
        advice, candidate = advisor.review_evidence(
            self.packet, self.current, self._empty_scouting(), None
        )
        self.assertIsNotNone(advice)
        self.assertIsNotNone(candidate)
        self.assertIn("claim-shoot-observed", advice.evidence_ids)

    def test_usage_receipt_captured(self):
        advisor = self._make_advisor()
        advisor.review_evidence(self.packet, self.current, self._empty_scouting(), None)
        receipt = advisor.last_receipt
        self.assertIsNotNone(receipt)
        assert receipt is not None
        self.assertEqual(receipt.input_tokens, 100)
        self.assertEqual(receipt.output_tokens, 50)
        self.assertEqual(receipt.model_id, "anthropic.claude-3-5-sonnet-20241022-v2:0")
        self.assertEqual(receipt.cache_mode, "off")
        self.assertIsNone(receipt.cache_read_tokens)
        self.assertIsNone(receipt.cache_write_tokens)

    def test_request_id_captured_in_receipt(self):
        advisor = self._make_advisor()
        advisor.review_evidence(self.packet, self.current, self._empty_scouting(), None)
        receipt = advisor.last_receipt
        assert receipt is not None
        self.assertEqual(receipt.request_id, "fake-request-id-001")

    def test_out_of_bounds_settings_raises_service_error(self):
        from agent.advisor.offline.contracts import OfflineServiceError
        from agent.model import DEFAULT_STYLE

        bad_json = json.dumps(
            {
                "settings": {"pass_margin": 3.0, "shot_range": 99.0, "press_radius": 22.0, "support_width": 15.0},
                "style": asdict(DEFAULT_STYLE),
                "rationale": "out of bounds proposal",
                "evidence_ids": ["claim-shoot-observed"],
                "uncertainties": [],
                "counter_tests": [],
            }
        )
        client = self.FakeBedrockClient(response_json=bad_json)
        advisor = self._make_advisor(client)
        with self.assertRaises(OfflineServiceError) as ctx:
            advisor.review_evidence(self.packet, self.current, self._empty_scouting(), None)
        self.assertEqual(ctx.exception.service, "bedrock")
        self.assertEqual(ctx.exception.stage, "review_evidence")

    def test_unknown_evidence_id_raises_service_error(self):
        from agent.advisor.offline.contracts import OfflineServiceError
        from agent.model import DEFAULT_SETTINGS, DEFAULT_STYLE

        bad_json = json.dumps(
            {
                "settings": asdict(DEFAULT_SETTINGS),
                "style": asdict(DEFAULT_STYLE),
                "rationale": "cites a phantom claim",
                "evidence_ids": ["nonexistent-claim-id"],
                "uncertainties": [],
                "counter_tests": [],
            }
        )
        client = self.FakeBedrockClient(response_json=bad_json)
        advisor = self._make_advisor(client)
        with self.assertRaises(OfflineServiceError) as ctx:
            advisor.review_evidence(self.packet, self.current, self._empty_scouting(), None)
        self.assertIn("UNKNOWN_EVIDENCE_IDS", str(ctx.exception.error_code))

    def test_empty_response_raises_service_error(self):
        from agent.advisor.offline.contracts import OfflineServiceError

        client = self.FakeBedrockClient(response_json="")
        advisor = self._make_advisor(client)
        with self.assertRaises(OfflineServiceError) as ctx:
            advisor.review_evidence(self.packet, self.current, self._empty_scouting(), None)
        self.assertEqual(ctx.exception.error_code, "EMPTY_RESPONSE")

    def test_bad_stop_reason_raises_service_error(self):
        from agent.advisor.offline.contracts import OfflineServiceError

        client = self.FakeBedrockClient(stop_reason="max_tokens")
        advisor = self._make_advisor(client)
        with self.assertRaises(OfflineServiceError) as ctx:
            advisor.review_evidence(self.packet, self.current, self._empty_scouting(), None)
        self.assertEqual(ctx.exception.error_code, "UNEXPECTED_STOP_REASON")

    def test_invalid_json_raises_service_error(self):
        from agent.advisor.offline.contracts import OfflineServiceError

        client = self.FakeBedrockClient(response_json="not-valid-json{{{")
        advisor = self._make_advisor(client)
        with self.assertRaises(OfflineServiceError) as ctx:
            advisor.review_evidence(self.packet, self.current, self._empty_scouting(), None)
        self.assertEqual(ctx.exception.error_code, "INVALID_JSON")

    def test_client_exception_raises_service_error(self):
        from agent.advisor.offline.contracts import OfflineServiceError

        client = self.FakeBedrockClient(side_effect=RuntimeError("network timeout"))
        advisor = self._make_advisor(client)
        with self.assertRaises(OfflineServiceError) as ctx:
            advisor.review_evidence(self.packet, self.current, self._empty_scouting(), None)
        self.assertEqual(ctx.exception.stage, "review_evidence")

    def test_converse_called_with_correct_model_id(self):
        client = self.FakeBedrockClient()
        advisor = self._make_advisor(client)
        advisor.review_evidence(self.packet, self.current, self._empty_scouting(), None)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["modelId"], "anthropic.claude-3-5-sonnet-20241022-v2:0")

    def test_structured_output_config_sent_when_enabled(self):
        client = self.FakeBedrockClient()
        advisor = self._make_advisor(client, structured_outputs=True)
        advisor.review_evidence(self.packet, self.current, self._empty_scouting(), None)
        call_kwargs = client.calls[0]
        self.assertIn("outputConfig", call_kwargs)
        schema = call_kwargs["outputConfig"]["textFormat"]["structure"]["jsonSchema"]
        self.assertEqual(schema["name"], "offline_football_advice")

    def test_structured_output_not_sent_by_default(self):
        client = self.FakeBedrockClient()
        advisor = self._make_advisor(client)
        advisor.review_evidence(self.packet, self.current, self._empty_scouting(), None)
        self.assertNotIn("outputConfig", client.calls[0])

    def test_reply_cannot_change_packet_source_digests(self):
        original_digests = dict(self.packet.source_digests)
        advisor = self._make_advisor()
        advisor.review_evidence(self.packet, self.current, self._empty_scouting(), None)
        self.assertEqual(self.packet.source_digests, original_digests)

    def test_reply_cannot_change_packet_claims(self):
        original_claims = self.packet.claims
        advisor = self._make_advisor()
        advisor.review_evidence(self.packet, self.current, self._empty_scouting(), None)
        self.assertEqual(self.packet.claims, original_claims)


@unittest.skipUnless(HAS_LANGCHAIN, "install the llm extra")
class LegacyAdvisorPreservationTest(unittest.TestCase):
    """Legacy Advisor.pick_archetype() and review_match() must still work via _ConverseInvoker."""

    def setUp(self) -> None:
        from agent.advisor.offline.bedrock import GroundedAdvisor  # noqa: PLC0415
        from agent.model import Profile
        from tests.offline_support import FakeBedrockClient, make_config

        self.GroundedAdvisor = GroundedAdvisor
        self.FakeBedrockClient = FakeBedrockClient
        self.config = make_config()
        self.baseline = Profile()

    def test_pick_archetype_still_works(self):

        pick_json = json.dumps(
            {
                "archetype": "baseline",
                "coach_policy": "none",
                "rationale": "best archetype given opponent",
            }
        )
        client = self.FakeBedrockClient(response_json=pick_json)
        advisor = self.GroundedAdvisor(client, self.config, {"baseline": self.baseline})
        advice, candidate = advisor.pick_archetype({}, "opp-001")
        self.assertEqual(advice.archetype, "baseline")
        self.assertEqual(advice.coach_policy, "none")
        self.assertIsNotNone(candidate)

    def test_review_match_still_works(self):
        from agent.model import DEFAULT_SETTINGS, DEFAULT_STYLE

        review_json = json.dumps(
            {
                "settings": asdict(DEFAULT_SETTINGS),
                "style": asdict(DEFAULT_STYLE),
                "rationale": "no change needed",
            }
        )
        client = self.FakeBedrockClient(response_json=review_json)
        advisor = self.GroundedAdvisor(client, self.config, {})
        advice, candidate = advisor.review_match({"score": [0, 1]}, self.baseline)
        self.assertEqual(advice.rationale, "no change needed")
        self.assertIsNotNone(candidate)


@unittest.skipUnless(HAS_LANGCHAIN, "install the llm extra")
class PromptCachingTest(unittest.TestCase):
    """Task 05: stable prefix, cachePoint injection, cache token receipts."""

    def setUp(self) -> None:
        from agent.advisor.offline.bedrock import _SYSTEM_PREFIX, GroundedAdvisor  # noqa: PLC0415
        from tests.offline_support import FakeBedrockClient, make_config, make_packet

        self.GroundedAdvisor = GroundedAdvisor
        self.SYSTEM_PREFIX = _SYSTEM_PREFIX
        self.FakeBedrockClient = FakeBedrockClient
        self.make_config = make_config
        self.make_packet = make_packet

    def _empty_scouting(self):
        from agent.advisor.offline.contracts import ScoutingContext

        return ScoutingContext(notes=(), extracted_text=(), excluded=())

    def test_stable_system_prefix_same_across_different_packets(self):
        from agent.model import Profile

        packet_a = self.make_packet(match_id="match-001", opponent_id="opp-a")
        packet_b = self.make_packet(match_id="match-002", opponent_id="opp-b")
        client = self.FakeBedrockClient()
        config = self.make_config()
        advisor = self.GroundedAdvisor(client, config, {"baseline": Profile()})

        advisor.review_evidence(packet_a, Profile(), self._empty_scouting(), None)
        advisor.review_evidence(packet_b, Profile(), self._empty_scouting(), None)

        # First system block text must be identical for both calls
        sys_a = client.calls[0]["system"][0]["text"]
        sys_b = client.calls[1]["system"][0]["text"]
        self.assertEqual(sys_a, sys_b)

        # User evidence content must differ
        user_a = client.calls[0]["messages"][0]["content"][0]["text"]
        user_b = client.calls[1]["messages"][0]["content"][0]["text"]
        self.assertNotEqual(user_a, user_b)

    def test_explicit_cache_mode_emits_cache_point(self):
        from agent.model import Profile

        config = self.make_config(cache_mode="explicit", explicit_cache=True)
        packet = self.make_packet()
        client = self.FakeBedrockClient()
        advisor = self.GroundedAdvisor(client, config, {"baseline": Profile()})
        advisor.review_evidence(packet, Profile(), self._empty_scouting(), None)

        system_blocks = client.calls[0]["system"]
        self.assertEqual(len(system_blocks), 2)
        self.assertEqual(system_blocks[1]["cachePoint"]["type"], "default")

    def test_off_cache_mode_no_cache_point(self):
        from agent.model import Profile

        config = self.make_config(cache_mode="off")
        packet = self.make_packet()
        client = self.FakeBedrockClient()
        advisor = self.GroundedAdvisor(client, config, {"baseline": Profile()})
        advisor.review_evidence(packet, Profile(), self._empty_scouting(), None)

        system_blocks = client.calls[0]["system"]
        self.assertEqual(len(system_blocks), 1)
        self.assertNotIn("cachePoint", system_blocks[0])

    def test_cache_read_tokens_captured(self):
        from agent.model import Profile

        config = self.make_config(cache_mode="explicit", explicit_cache=True)
        packet = self.make_packet()
        client = self.FakeBedrockClient(
            cache_tokens={"cacheReadInputTokens": 80, "cacheWriteInputTokens": 20}
        )
        advisor = self.GroundedAdvisor(client, config, {"baseline": Profile()})
        advisor.review_evidence(packet, Profile(), self._empty_scouting(), None)

        receipt = advisor.last_receipt
        assert receipt is not None
        self.assertEqual(receipt.cache_read_tokens, 80)
        self.assertEqual(receipt.cache_write_tokens, 20)

    def test_absent_cache_token_fields_are_none_not_zero(self):
        from agent.model import Profile

        config = self.make_config()
        packet = self.make_packet()
        client = self.FakeBedrockClient()  # no cache_tokens set
        advisor = self.GroundedAdvisor(client, config, {"baseline": Profile()})
        advisor.review_evidence(packet, Profile(), self._empty_scouting(), None)

        receipt = advisor.last_receipt
        assert receipt is not None
        self.assertIsNone(receipt.cache_read_tokens)
        self.assertIsNone(receipt.cache_write_tokens)

    def test_implicit_cache_mode_no_cache_point(self):
        from agent.model import Profile

        config = self.make_config(cache_mode="implicit")
        packet = self.make_packet()
        client = self.FakeBedrockClient()
        advisor = self.GroundedAdvisor(client, config, {"baseline": Profile()})
        advisor.review_evidence(packet, Profile(), self._empty_scouting(), None)

        system_blocks = client.calls[0]["system"]
        # implicit mode: no explicit checkpoint, provider may use its own
        self.assertEqual(len(system_blocks), 1)


if __name__ == "__main__":
    unittest.main()
