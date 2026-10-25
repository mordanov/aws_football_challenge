"""Read-only S3 reader for the training monitor. No imports from football_agent/."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

_S3_PREFIX = "final-agent-learning"
_SETTINGS_FIELDS = ("press_radius", "pass_margin", "shot_range", "support_width")
_CHAIN_CAP = 50


class RegistryMissing(Exception):
    pass


class S3Reader:
    def __init__(self, client: Any, bucket: str, team_id: str, owner_account: str) -> None:
        self._client = client
        self._bucket = bucket
        self._team = team_id
        self._owner = owner_account
        self._reg = f"{_S3_PREFIX}/{team_id}/registry/"

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _get(self, key: str) -> dict | None:
        """Return parsed JSON body, or None on NoSuchKey."""
        try:
            resp = self._client.get_object(
                Bucket=self._bucket, Key=key, ExpectedBucketOwner=self._owner
            )
            return json.loads(resp["Body"].read())
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404", "NoSuchBucket"):
                return None
            raise

    def _head(self, key: str) -> datetime | None:
        """Return S3 LastModified for key, or None on missing."""
        try:
            resp = self._client.head_object(
                Bucket=self._bucket, Key=key, ExpectedBucketOwner=self._owner
            )
            return resp["LastModified"]
        except Exception as exc:
            resp_attr = getattr(exc, "response", None)
            if resp_attr:
                err_code = resp_attr.get("Error", {}).get("Code", "")
                if err_code in ("NoSuchKey", "404", "403"):
                    return None
            return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def current_profile(self) -> dict:
        """Return current agent profile info.

        Raises RegistryMissing if ACTIVE.json does not exist.
        """
        active = self._get(self._reg + "ACTIVE.json")
        if active is None:
            raise RegistryMissing("ACTIVE.json not found — registry not bootstrapped")

        digest = active["digest"]
        profile_doc = self._get(self._reg + f"profiles/{digest}.json") or {}
        profile = profile_doc.get("profile", {})
        settings_raw = profile.get("settings", {})

        ts = self._head(active["commit"])

        return {
            "version": profile.get("version", "unknown"),
            "generation": active.get("generation"),
            "digest_short": digest[:8],
            "active_since": ts.isoformat() if ts else None,
            "settings": {f: settings_raw.get(f) for f in _SETTINGS_FIELDS},
        }

    def promotion_history(self) -> list[dict]:
        """Return promotion history, newest first, capped at _CHAIN_CAP.

        Each entry: {generation, date, changes, status, reject_reason}
        """
        active = self._get(self._reg + "ACTIVE.json")
        if active is None:
            raise RegistryMissing("ACTIVE.json not found — registry not bootstrapped")

        # Walk the generation chain; collect (gen_number, gen_key, gen_doc) tuples
        chain: list[tuple[int, str, dict]] = []
        current_key = active["commit"]
        seen_keys: set[str] = set()

        for _ in range(_CHAIN_CAP):
            if current_key in seen_keys:
                break  # circular chain guard
            seen_keys.add(current_key)

            gen_doc = self._get(current_key)
            if gen_doc is None:
                break

            chain.append((gen_doc.get("generation", 0), current_key, gen_doc))
            pred = gen_doc.get("predecessor")
            if pred is None:
                break
            current_key = pred

        if not chain:
            return []

        # Load profiles for each generation to compute diffs
        # chain[0] is newest, chain[-1] is oldest
        def _load_settings(gen_doc: dict) -> dict:
            d = gen_doc.get("digest", "")
            pdoc = self._get(self._reg + f"profiles/{d}.json") or {}
            return pdoc.get("profile", {}).get("settings", {})

        result = []
        for i, (gen_num, gen_key, gen_doc) in enumerate(chain):
            # Date from S3 LastModified
            ts = self._head(gen_key)
            date_str = ts.isoformat() if ts else None

            # Settings diff vs predecessor
            changes: dict = {}
            if i + 1 < len(chain):
                pred_gen_doc = chain[i + 1][2]
                curr_settings = _load_settings(gen_doc)
                pred_settings = _load_settings(pred_gen_doc)
                for field in _SETTINGS_FIELDS:
                    cv = curr_settings.get(field)
                    pv = pred_settings.get(field)
                    if cv != pv and cv is not None and pv is not None:
                        changes[field] = {"before": pv, "after": cv}

            # Status: head generation is "promoted" (it IS the active one)
            if i == 0:
                status = "promoted"
                reject_reason = None
            else:
                result_doc = self._get(self._reg + f"results/{gen_num}.json")
                if result_doc is None:
                    status = "no_attempt" if i == len(chain) - 1 else "pending"
                    reject_reason = None
                else:
                    status = result_doc.get("status", "unknown")
                    reject_reason = None
                    eval_ref = result_doc.get("evaluation_ref")
                    if eval_ref and status == "rejected":
                        eval_doc = self._get(eval_ref)
                        if eval_doc:
                            reject_reason = eval_doc.get("reason")

            result.append(
                {
                    "generation": gen_num,
                    "date": date_str,
                    "changes": changes,
                    "status": status,
                    "reject_reason": reject_reason,
                }
            )

        return result

    def match_activity(self, days: int = 8) -> list[dict]:
        """Return match counts per day for the last `days` days, oldest first."""
        obs_prefix = f"{_S3_PREFIX}/{self._team}/observations/_days/"
        resp = self._client.list_objects_v2(Bucket=self._bucket, Prefix=obs_prefix)
        contents = resp.get("Contents", [])

        # Parse match_ids per date from keys: {prefix}{date}/{match_id}/{side}/{obs_id}
        counts: dict[str, set[str]] = {}
        for item in contents:
            key = item["Key"]
            relative = key[len(obs_prefix):]
            parts = relative.split("/")
            if len(parts) >= 2:
                day_str, match_id = parts[0], parts[1]
                counts.setdefault(day_str, set()).add(match_id)

        today = datetime.now(timezone.utc).date()
        return [
            {
                "date": (today - timedelta(days=days - 1 - i)).isoformat(),
                "match_count": len(counts.get((today - timedelta(days=days - 1 - i)).isoformat(), set())),
            }
            for i in range(days)
        ]
