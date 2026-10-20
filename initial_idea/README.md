# AFC Agent

Rules-first, LLM-optional agent for the AWS Agentic Football Cup (5v5).
See STRATEGY.md for the reasoning and the list of unverified assumptions.

    python3 -m unittest discover -s tests -v     # 10 tests, no AWS needed

Layout: `agent/adapter.py` (raw state -> world; edit first), `agent/commands.py`
(wire format; edit second), `agent/tactics.py` (math, tunable constants),
`agent/policy.py` (decisions), `agent/llm_layer.py` (optional Strands/Bedrock
tie-break), `agent/main.py` (AgentCore entrypoint, one deploy per player,
`MY_PLAYER_ID` = 0..4).

Enable the LLM: `LLM_ENABLED=1 LLM_MODEL_ID=us.amazon.nova-micro-v1:0`
(needs `strands-agents` and Bedrock access). Off by default.
