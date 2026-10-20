"""AgentCore entrypoint script (agent/main.py uses package-relative imports)."""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent.main import app  # noqa: E402

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if app is None:
        raise SystemExit("pip install bedrock-agentcore")
    app.run()
