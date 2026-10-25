"""Training monitor: Flask server + S3 reader for the football agent learning pipeline."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import boto3
from flask import Flask, jsonify, send_from_directory

from monitor.s3_reader import S3Reader, RegistryMissing

_HERE = Path(__file__).parent


def create_app(bucket: str, team_id: str, owner_account: str) -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def index():
        return send_from_directory(str(_HERE), "index.html")

    @app.get("/api/status")
    def status():
        try:
            client = boto3.client("s3")
            reader = S3Reader(client, bucket, team_id, owner_account)
            return jsonify(
                {
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "team_id": team_id,
                    "current_profile": reader.current_profile(),
                    "promotion_history": reader.promotion_history(),
                    "match_activity": reader.match_activity(),
                }
            )
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    return app


def main() -> None:
    p = argparse.ArgumentParser(description="Football agent training monitor")
    p.add_argument("--config", required=True, help="Path to deploy config JSON (s3_learning.json)")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args()

    cfg = json.loads(Path(args.config).read_text())
    app = create_app(cfg["artifact_bucket"], cfg["team_id"], cfg["account"])
    print(f"Training monitor → http://127.0.0.1:{args.port}/")
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
