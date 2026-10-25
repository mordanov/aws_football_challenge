"""AgentCore Runtime entry for one final_agent player.

deploy.py stages the `agent` package and the shipped profile next to this file.
"""

import os
from pathlib import Path

os.environ.setdefault("AFC_PROFILE", str(Path(__file__).with_name("profile.json")))

from agent.main import main  # noqa: E402

if __name__ == "__main__":
    main()
