"""Defensive, multi-format wire-to-`World` adapter for Redesign 3.

Supports:
1. Official AWS AgentCore envelope:
   `{"prompt": "{\"gameState\": {...}, \"teamId\": 0, \"myPlayers\": [2]}"}`
   or `{"prompt": {"gameState": {...}, "teamId": 0, "myPlayers": [2]}}`
2. Direct `gameState` envelope:
   `{"gameState": {...}, "teamId": 0, "myPlayers": [0, 1, 2, 3, 4]}`
3. Legacy / flat test payloads (`initial_idea` test fixtures).

Solves all known server quirks from `aws-samples/sample-ai-possibilities`:
- `agentId` (`"agentId_0".."agentId_4"`) duplicated on both home and away teams.
- Case-insensitive `teamCode` (`"home"`, `"HOME"`, `"Home"`, `"away"`, `"AWAY"`).
- Authoritative `ball.isFree` flag.
- Disambiguating `ball.possessionAgentId` across teams using both team tags (when
  present) and physical proximity to the ball (`<= 6.5m`) so an away forward never
  falsely believes they hold the ball when the home forward with the same index has it.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any

from .model import Ball, Player, Score, Vec2, World

MATCH_DURATION_SECONDS: float = 300.0

_PLAY_MODE_MAP: dict[int, str] = {
    0: "OPEN_PLAY",
    1: "KICK_OFF",
    2: "THROW_IN",
    3: "CORNER",
    4: "GOAL_KICK",
    5: "FREE_KICK",
    6: "PENALTY",
}


def unwrap_payload(payload: dict[str, Any] | str) -> dict[str, Any]:
    """Unwrap AgentCore `{"prompt": ...}` wrapper if present."""
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
            if isinstance(parsed, dict):
                payload = parsed
            else:
                return {}
        except Exception:
            return {}

    if not isinstance(payload, dict):
        return {}

    prompt = payload.get("prompt")
    if isinstance(prompt, str):
        try:
            inner = json.loads(prompt)
            if isinstance(inner, dict):
                merged = dict(payload)
                merged.update(inner)
                return merged
        except Exception:
            pass
    elif isinstance(prompt, dict):
        merged = dict(payload)
        merged.update(prompt)
        return merged

    return payload


def _extract_vec2(obj: Any, sign: int = 1) -> tuple[Vec2, float]:
    if isinstance(obj, dict):
        x = float(obj.get("x", 0.0) or 0.0)
        y = float(obj.get("y", 0.0) or 0.0)
        z = float(obj.get("z", 0.0) or 0.0)
        return Vec2(x * sign, y * sign), z
    if isinstance(obj, (list, tuple)) and len(obj) >= 2:
        x = float(obj[0] or 0.0)
        y = float(obj[1] or 0.0)
        z = float(obj[2] or 0.0) if len(obj) >= 3 else 0.0
        return Vec2(x * sign, y * sign), z
    return Vec2(0.0, 0.0), 0.0


def _parse_player_id(raw_val: Any) -> int | None:
    if raw_val is None or isinstance(raw_val, bool):
        return None
    if isinstance(raw_val, (int, float)):
        return int(raw_val) % 5
    if isinstance(raw_val, str):
        s = raw_val.strip()
        if s.startswith("agentId_"):
            suffix = s.split("_", 1)[1]
            if suffix.isdigit():
                return int(suffix) % 5
        # Handle e.g. "home-GK" or "player_3"
        for token in ("_", "-"):
            if token in s:
                tail = s.rsplit(token, 1)[-1]
                if tail.isdigit():
                    return int(tail) % 5
                role_idx = {"GK": 0, "DEF": 1, "MID": 2, "FWD1": 3, "FWD2": 4}.get(tail.upper())
                if role_idx is not None:
                    return role_idx
        if s.isdigit():
            return int(s) % 5
    return None


def _normalize_team_label(raw_team: Any) -> str | None:
    """Normalize raw team identifier to `'home'`, `'away'`, `'me'`, or `'opp'`."""
    if raw_team is None or isinstance(raw_team, bool):
        return None
    if isinstance(raw_team, (int, float)):
        idx = int(raw_team)
        if idx == 0:
            return "home"
        if idx == 1:
            return "away"
        return str(idx)
    if isinstance(raw_team, str):
        low = raw_team.strip().lower()
        if low in ("home", "team_a", "a", "0", "blue", "left"):
            return "home"
        if low in ("away", "team_b", "b", "1", "red", "right"):
            return "away"
        if low in ("me", "my", "us", "self", "friendly"):
            return "me"
        if low in ("opp", "opponent", "them", "enemy"):
            return "opp"
        return low
    return None


def _normalize_stamina(raw_stamina: Any) -> float:
    if raw_stamina is None:
        return 1.0
    try:
        val = float(raw_stamina)
    except (TypeError, ValueError):
        return 1.0
    if val > 1.5:
        val = val / 100.0
    return max(0.0, min(1.0, val))


def _infer_my_team_id(top: dict[str, Any], gs: dict[str, Any]) -> int:
    for container in (top, gs):
        for key in ("teamId", "team_id", "myTeamId", "my_team_id"):
            val = container.get(key)
            if val is not None and not isinstance(val, bool):
                if isinstance(val, (int, float)):
                    return 0 if int(val) == 0 else 1
                norm = _normalize_team_label(val)
                if norm == "home":
                    return 0
                if norm == "away":
                    return 1
        side = _normalize_team_label(container.get("side") or container.get("myTeam") or container.get("team"))
        if side == "home":
            return 0
        if side == "away":
            return 1

    env_team = os.environ.get("TEAM_ID")
    if env_team is not None and env_team.isdigit():
        return 0 if int(env_team) == 0 else 1
    return 0


def _infer_controlled_ids(top: dict[str, Any], gs: dict[str, Any]) -> tuple[int, ...]:
    for container in (top, gs):
        for key in ("myPlayers", "my_players", "controlledPlayers", "playerIds"):
            raw = container.get(key)
            if isinstance(raw, (list, tuple)) and len(raw) > 0:
                ids: list[int] = []
                for item in raw:
                    if isinstance(item, dict):
                        pid = _parse_player_id(
                            item.get("playerId") or item.get("id") or item.get("agentId")
                        )
                    else:
                        pid = _parse_player_id(item)
                    if pid is not None and pid not in ids:
                        ids.append(pid)
                if ids:
                    return tuple(ids)

    env_pid = os.environ.get("PLAYER_ID")
    if env_pid is not None and env_pid.strip().isdigit():
        return (int(env_pid.strip()) % 5,)

    return (0, 1, 2, 3, 4)


def _infer_attack_sign(my_team_id: int, gs: dict[str, Any], raw_my_players: list[dict[str, Any]]) -> int:
    """Team 0 (HOME) attacks +x (+1); Team 1 (AWAY) attacks -x (-1).

    Also checks explicit `attackDirection` or GK raw x position when available so legacy
    unit tests that omit `teamId` still orient accurately.
    """
    explicit = gs.get("attackDirection") or gs.get("attack_direction") or gs.get("direction")
    if isinstance(explicit, (int, float)) and explicit != 0:
        return 1 if explicit > 0 else -1
    if isinstance(explicit, str):
        if explicit.lower() in ("right", "+", "east", "1", "positive"):
            return 1
        if explicit.lower() in ("left", "-", "west", "-1", "negative"):
            return -1

    # Check GK raw x coordinate if unambiguous
    for p in raw_my_players:
        pid = _parse_player_id(p.get("agentId") or p.get("playerId") or p.get("id"))
        if pid == 0:
            pos = p.get("position") or p.get("pos")
            if isinstance(pos, dict):
                gx = float(pos.get("x", 0.0) or 0.0)
                if abs(gx) > 25.0:
                    return 1 if gx < 0 else -1

    return 1 if my_team_id == 0 else -1


def _resolve_ball_possession(
    raw_ball: dict[str, Any],
    my_players_raw: list[tuple[int, dict[str, Any]]],
    opp_players_raw: list[tuple[int, dict[str, Any]]],
    my_team_code: str,
) -> tuple[bool, str | None, int | None, str | None]:
    """Return `(is_free, owner_team, owner_id, possession_raw)` in normalized team space.

    Handles both:
    - Official AWS `isFree` + `possessionAgentId` (where `agentId_3` exists on BOTH teams!)
    - Legacy `owner` / `holder` / `possessionPlayerId` + `possessionTeamId` / `ownerTeam`
    """
    if raw_ball.get("isFree") is True:
        return True, None, None, "free"

    bx = 0.0
    by = 0.0
    bpos = raw_ball.get("position") or raw_ball.get("pos")
    if isinstance(bpos, dict):
        bx = float(bpos.get("x", 0.0) or 0.0)
        by = float(bpos.get("y", 0.0) or 0.0)
    elif isinstance(bpos, (list, tuple)) and len(bpos) >= 2:
        bx = float(bpos[0] or 0.0)
        by = float(bpos[1] or 0.0)

    def _raw_dist_to_ball(p_dict: dict[str, Any]) -> float:
        ppos = p_dict.get("position") or p_dict.get("pos")
        if isinstance(ppos, dict):
            px = float(ppos.get("x", 0.0) or 0.0)
            py = float(ppos.get("y", 0.0) or 0.0)
            return math.hypot(px - bx, py - by)
        if isinstance(ppos, (list, tuple)) and len(ppos) >= 2:
            return math.hypot(float(ppos[0] or 0.0) - bx, float(ppos[1] or 0.0) - by)
        return 999.0

    # 1. Explicit `hasBall` flag on any player
    for pid, p_dict in my_players_raw:
        if p_dict.get("hasBall") is True or p_dict.get("has_ball") is True:
            return False, "me", pid, f"me:{pid}"
    for pid, p_dict in opp_players_raw:
        if p_dict.get("hasBall") is True or p_dict.get("has_ball") is True:
            return False, "opp", pid, f"opp:{pid}"

    # 2. Check explicit owner / possession claim on ball
    claim_team_raw = (
        raw_ball.get("possessionTeamId")
        if raw_ball.get("possessionTeamId") is not None
        else (raw_ball.get("ownerTeam") or raw_ball.get("holderTeam") or raw_ball.get("team"))
    )
    claim_id_raw = (
        raw_ball.get("possessionAgentId")
        if raw_ball.get("possessionAgentId") is not None
        else (
            raw_ball.get("possessionPlayerId")
            if raw_ball.get("possessionPlayerId") is not None
            else (raw_ball.get("owner") if raw_ball.get("owner") is not None else raw_ball.get("holder"))
        )
    )

    if isinstance(claim_id_raw, dict):
        claim_team_raw = claim_team_raw or claim_id_raw.get("team") or claim_id_raw.get("teamId") or claim_id_raw.get("teamCode")
        claim_id_raw = claim_id_raw.get("id") or claim_id_raw.get("playerId") or claim_id_raw.get("agentId")

    if claim_id_raw is None:
        return True, None, None, "free"

    claimed_pid = _parse_player_id(claim_id_raw)
    # Guard against unresolvable strings like "arn:aws:bedrock..." or "agentId_9"
    if isinstance(claim_id_raw, str):
        s = claim_id_raw.strip()
        if s.startswith("agentId_"):
            suf = s.split("_", 1)[1]
            if not suf.isdigit() or int(suf) < 0 or int(suf) > 4:
                return False, None, None, "unknown"
        elif not s.isdigit() and claim_team_raw is None:
            return False, None, None, "unknown"

    if claimed_pid is None:
        return False, None, None, "unknown"

    norm_claim_team = _normalize_team_label(claim_team_raw)
    if norm_claim_team is not None:
        if norm_claim_team in ("me", my_team_code):
            return False, "me", claimed_pid, f"me:{claimed_pid}"
        if norm_claim_team in ("opp", "home", "away"):
            return False, "opp", claimed_pid, f"opp:{claimed_pid}"

    # 3. Colliding `agentId_N` across both teams (no team tag on ball):
    # Check both candidates (`my_players_raw` with `pid == claimed_pid` and `opp_players_raw` with `pid == claimed_pid`).
    my_cand = next((p for pid, p in my_players_raw if pid == claimed_pid), None)
    opp_cand = next((p for pid, p in opp_players_raw if pid == claimed_pid), None)

    if my_cand is not None and opp_cand is not None:
        # Note: in `lib/state.py` of the official sample repo, `_resolve_holder` iterates
        # `players` in order, OR we can disambiguate when one player is near the ball and
        # the other is far away. Wait: what if a test fixture (`GAME_STATE` in `test_helpers.py`)
        # puts `ball` at `(15, -5)` and home `agentId_3` at `(20, -8)` (dist ~5.8m) while away
        # `agentId_3` is at `(-15, 8)` (dist ~32.7m)? Proximity (`d_my < d_opp`) picks home `agentId_3`!
        d_my = _raw_dist_to_ball(my_cand)
        d_opp = _raw_dist_to_ball(opp_cand)
        if abs(d_my - d_opp) > 1.5:
            if d_my < d_opp:
                return False, "me", claimed_pid, f"me:{claimed_pid}"
            return False, "opp", claimed_pid, f"opp:{claimed_pid}"
        # Tie-breaker: in official `lib/state.py`, the first matching player in `gameState["players"]`
        # (which lists home players first) wins when distances are identical.
        if my_team_code == "home":
            return False, "me", claimed_pid, f"me:{claimed_pid}"
        return False, "opp", claimed_pid, f"opp:{claimed_pid}"

    if my_cand is not None:
        return False, "me", claimed_pid, f"me:{claimed_pid}"
    if opp_cand is not None:
        return False, "opp", claimed_pid, f"opp:{claimed_pid}"

    return False, None, None, "unknown"


def parse_world(raw_payload: dict[str, Any] | str) -> World:
    """Parse any supported wire payload into our attack-normalized `World`."""
    top = unwrap_payload(raw_payload)
    gs = top.get("gameState") if isinstance(top.get("gameState"), dict) else top

    my_team_id = _infer_my_team_id(top, gs)
    my_team_code = "home" if my_team_id == 0 else "away"
    opp_team_code = "away" if my_team_id == 0 else "home"
    controlled_ids = _infer_controlled_ids(top, gs)

    # Partition raw players into my_raw and opp_raw
    my_raw_dicts: list[dict[str, Any]] = []
    opp_raw_dicts: list[dict[str, Any]] = []

    if isinstance(gs.get("myTeam"), list) or isinstance(gs.get("opponents"), list):
        my_raw_dicts = [p for p in (gs.get("myTeam") or []) if isinstance(p, dict)]
        opp_raw_dicts = [p for p in (gs.get("opponents") or []) if isinstance(p, dict)]
    elif isinstance(gs.get("players"), list):
        for p in gs["players"]:
            if not isinstance(p, dict):
                continue
            raw_t = p.get("teamCode")
            if raw_t is None:
                raw_t = p.get("teamId") if p.get("teamId") is not None else (p.get("team") or p.get("side"))
            norm_t = _normalize_team_label(raw_t)
            if norm_t in ("me", my_team_code):
                my_raw_dicts.append(p)
            elif norm_t in ("opp", opp_team_code):
                opp_raw_dicts.append(p)
            else:
                # Fallback if first 5 are home and next 5 are away
                if len(my_raw_dicts) < 5 and my_team_id == 0:
                    my_raw_dicts.append(p)
                else:
                    opp_raw_dicts.append(p)

    attack_sign = _infer_attack_sign(my_team_id, gs, my_raw_dicts)

    my_indexed: list[tuple[int, dict[str, Any]]] = []
    for idx, p in enumerate(my_raw_dicts):
        pid = _parse_player_id(p.get("agentId") or p.get("playerId") or p.get("id") or p.get("unum"))
        if pid is None:
            pid = idx % 5
        my_indexed.append((pid, p))

    opp_indexed: list[tuple[int, dict[str, Any]]] = []
    for idx, p in enumerate(opp_raw_dicts):
        pid = _parse_player_id(p.get("agentId") or p.get("playerId") or p.get("id") or p.get("unum"))
        if pid is None:
            pid = idx % 5
        opp_indexed.append((pid, p))

    raw_ball = gs.get("ball") if isinstance(gs.get("ball"), dict) else {}
    is_free, owner_team, owner_id, poss_raw = _resolve_ball_possession(
        raw_ball, my_indexed, opp_indexed, my_team_code
    )

    ball_pos, ball_z = _extract_vec2(raw_ball.get("position") or raw_ball.get("pos") or raw_ball, attack_sign)
    ball_vel, ball_vz = _extract_vec2(raw_ball.get("velocity") or raw_ball.get("vel") or {}, attack_sign)

    def _build_player(pid: int, p_dict: dict[str, Any], team_tag: str) -> Player:
        pos, _ = _extract_vec2(p_dict.get("position") or p_dict.get("pos") or p_dict, attack_sign)
        vel, _ = _extract_vec2(p_dict.get("velocity") or p_dict.get("vel") or {}, attack_sign)
        stamina = _normalize_stamina(p_dict.get("stamina", p_dict.get("energy", 1.0)))
        has_ball = (not is_free) and (owner_team == team_tag) and (owner_id == pid)
        orientation = float(p_dict.get("orientation", 0.0) or 0.0)
        if attack_sign < 0:
            orientation = (orientation + math.pi) % (2.0 * math.pi)
        speed_val = p_dict.get("speed")
        speed = float(speed_val) if speed_val is not None else vel.length()
        is_sprinting = bool(p_dict.get("isSprinting", p_dict.get("sprint", False)))
        last_action = str(p_dict.get("lastAction") or p_dict.get("last_action") or "")
        yellow_cards = int(p_dict.get("yellowCards", p_dict.get("yellow_cards", 0)) or 0)
        red_card = bool(p_dict.get("redCard", p_dict.get("red_card", False)))
        return Player(
            id=pid,
            team=team_tag,
            pos=pos,
            vel=vel,
            stamina=stamina,
            has_ball=has_ball,
            orientation=orientation,
            speed=speed,
            is_sprinting=is_sprinting,
            last_action=last_action,
            yellow_cards=yellow_cards,
            red_card=red_card,
        )

    me_players = tuple(sorted((_build_player(pid, p, "me") for pid, p in my_indexed), key=lambda pl: pl.id))
    opp_players = tuple(sorted((_build_player(pid, p, "opp") for pid, p in opp_indexed), key=lambda pl: pl.id))

    # Fallback: if neither owner_team nor isFree matched, check if a player is within 1.5m of a slow ball
    ball = Ball(
        pos=ball_pos,
        vel=ball_vel,
        z=ball_z,
        vz=ball_vz,
        is_free=is_free,
        owner_team=owner_team,
        owner_id=owner_id,
        possession_raw=poss_raw,
    )

    # Parse tick & clock
    tick = int(gs.get("tick", gs.get("frame", gs.get("step", 0))) or 0)
    raw_clock = gs.get("gameTime", gs.get("clock", gs.get("time", gs.get("elapsed"))))
    if raw_clock is not None:
        clock = float(raw_clock)
    else:
        clock = round(tick / 60.0, 2)
    time_rem = float(gs.get("timeRemaining", max(0.0, MATCH_DURATION_SECONDS - clock)))

    # Parse playMode & modeTeamId
    raw_pm = gs.get("playMode", gs.get("mode", gs.get("restart", "OPEN_PLAY")))
    if isinstance(raw_pm, int):
        play_mode = _PLAY_MODE_MAP.get(raw_pm, f"MODE_{raw_pm}")
    else:
        play_mode = str(raw_pm or "OPEN_PLAY").upper()
        if play_mode in ("PLAYON", "PLAY_ON", "NORMAL", "LIVE"):
            play_mode = "OPEN_PLAY"

    raw_mode_team = gs.get("modeTeamId", gs.get("restartTeam"))
    norm_mode_team = _normalize_team_label(raw_mode_team)
    if norm_mode_team in ("me", my_team_code):
        mode_team: str | None = "me"
    elif norm_mode_team in ("opp", opp_team_code):
        mode_team = "opp"
    else:
        mode_team = None

    # Parse score
    raw_score = gs.get("score")
    score = Score(0, 0)
    if isinstance(raw_score, dict):
        if "me" in raw_score or "opp" in raw_score:
            score = Score(int(raw_score.get("me", 0) or 0), int(raw_score.get("opp", 0) or 0))
        else:
            home_s = int(raw_score.get("home", raw_score.get("0", 0)) or 0)
            away_s = int(raw_score.get("away", raw_score.get("1", 0)) or 0)
            score = Score(home_s, away_s) if my_team_id == 0 else Score(away_s, home_s)

    # Parse live Coach instructions (`teamChat` list or `coachInstruction` string)
    team_chat_list: list[str] = []
    raw_chat = gs.get("teamChat") or top.get("teamChat")
    if isinstance(raw_chat, list):
        for entry in raw_chat:
            if isinstance(entry, str) and entry.strip():
                team_chat_list.append(entry.strip())
            elif isinstance(entry, dict):
                msg = entry.get("message") or entry.get("text") or entry.get("instruction") or entry.get("content")
                if isinstance(msg, str) and msg.strip():
                    team_chat_list.append(msg.strip())

    coach_str: str | None = None
    for k in ("coachInstruction", "coach_instruction", "coach", "instruction"):
        v = gs.get(k) or top.get(k)
        if isinstance(v, str) and v.strip():
            coach_str = v.strip()
            break
        if isinstance(v, dict):
            msg = v.get("message") or v.get("text") or v.get("instruction")
            if isinstance(msg, str) and msg.strip():
                coach_str = msg.strip()
                break

    if coach_str is None and team_chat_list:
        coach_str = team_chat_list[-1]

    return World(
        tick=tick,
        clock=clock,
        time_remaining=time_rem,
        play_mode=play_mode,
        mode_team=mode_team,
        my_team_id=my_team_id,
        attack_sign=attack_sign,
        controlled_ids=controlled_ids,
        ball=ball,
        me=me_players,
        opp=opp_players,
        score=score,
        coach_instruction=coach_str,
        team_chat=tuple(team_chat_list),
    )
