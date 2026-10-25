"""Fetch and analyze telemetry for a single match."""
import subprocess, os, json, sys
from datetime import datetime, timezone
from collections import Counter

CREDS_FILE = '/Users/aleksandr/Local/EPAM/aws_football_challenge/aws_creds.txt'

CW_GROUPS = {
    0: "/aws/bedrock-agentcore/runtimes/Redesign4_default_r4_gk-nxusigFggA-DEFAULT",
    1: "/aws/bedrock-agentcore/runtimes/Redesign4_default_r4_def-t1Kh1c7eeZ-DEFAULT",
    2: "/aws/bedrock-agentcore/runtimes/Redesign4_default_r4_mid-Nq5Hfb67re-DEFAULT",
    3: "/aws/bedrock-agentcore/runtimes/Redesign4_default_r4_fwd1-7vRt5lCgqV-DEFAULT",
    4: "/aws/bedrock-agentcore/runtimes/Redesign4_default_r4_fwd2-syVGGx7Rzk-DEFAULT",
}

creds = {}
with open(CREDS_FILE) as f:
    for line in f:
        line = line.strip()
        if line.startswith('export '):
            parts = line[7:].split('=', 1)
            if len(parts) == 2:
                k, v = parts[0], parts[1].strip().strip('"').strip("'")
                creds[k] = v
env = {**os.environ, **creds}
env.pop('AWS_MAX_ATTEMPTS', None)

def fetch_stream(group, stream_name):
    events = []
    token = None
    for _ in range(5):  # max 5 pages
        cmd = ['aws', 'logs', 'get-log-events',
               '--log-group-name', group,
               '--log-stream-name', stream_name,
               '--limit', '1000',
               '--region', 'us-east-1']
        if token:
            cmd += ['--next-token', token]
        r = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if r.returncode != 0:
            break
        data = json.loads(r.stdout)
        new_events = data.get('events', [])
        events.extend(new_events)
        new_token = data.get('nextForwardToken')
        if not new_token or new_token == token:
            break
        token = new_token
    return events

def analyze_match(match_id, seats):
    all_decisions = []
    all_errors = []

    for seat_key, seat_info in seats.items():
        group = seat_info['group']
        stream = seat_info['stream']
        pos = int(seat_key.split('_pos')[1])

        raw_events = fetch_stream(group, stream)
        for ev in raw_events:
            try:
                msg = json.loads(ev['message'])
                msg['_pos'] = pos
                msg['_seat'] = seat_key
                if msg.get('type') == 'decision':
                    all_decisions.append(msg)
                elif msg.get('type') == 'error':
                    all_errors.append(msg)
            except Exception:
                pass

    if not all_decisions:
        return None

    # Sort by tick
    all_decisions.sort(key=lambda d: d.get('tick', 0))

    # Get first/last snapshots
    first = all_decisions[0]
    last = all_decisions[-1]

    def get_snap(d):
        return d.get('snapshot') or {}

    first_snap = get_snap(first)
    last_snap = get_snap(last)

    # Detect team side (which team index we are)
    team_side = None
    for d in all_decisions[:20]:
        snap = get_snap(d)
        if snap.get('team') is not None:
            team_side = snap['team']
            break

    # Score progression - find score changes
    score_transitions = []
    prev_score = None
    for d in all_decisions:
        snap = get_snap(d)
        score = snap.get('score')
        if score and score != prev_score:
            score_transitions.append({
                'tick': d.get('tick'),
                'clock': snap.get('clock'),
                'score': score,
            })
            prev_score = score

    # Final score
    final_score = last_snap.get('score', [0, 0])
    final_clock = last_snap.get('clock', 0)
    duration = last_snap.get('duration', 120)

    # Our score vs opp score
    team_idx = team_side or 0
    our_score = final_score[team_idx] if len(final_score) > team_idx else 0
    opp_score = final_score[1 - team_idx] if len(final_score) > (1-team_idx) else 0

    result = 'W' if our_score > opp_score else ('L' if opp_score > our_score else 'D')

    # Command distribution
    cmd_counts = Counter()
    for d in all_decisions:
        ct = d.get('command', {}).get('commandType')
        if ct:
            cmd_counts[ct] += 1

    # Rush events
    rush_count = sum(1 for d in all_decisions if d.get('rush') == 1)

    # Stage events
    stage_counts = Counter(d.get('stage', 0) for d in all_decisions)

    # Possession analysis
    owned_decs = [d for d in all_decisions if (get_snap(d).get('owner') or [None])[0] is not None]
    our_poss = 0
    if owned_decs:
        ours = sum(1 for d in owned_decs if (get_snap(d).get('owner') or [None, None])[0] == team_idx)
        our_poss = round(100 * ours / len(owned_decs))

    # Decision latency
    latencies = [d.get('latency_ms') for d in all_decisions if d.get('latency_ms')]
    avg_latency = sum(latencies) / len(latencies) if latencies else None

    # Profile version
    profile_versions = set(d.get('profile_version') for d in all_decisions if d.get('profile_version'))

    # Error count
    error_count = len(all_errors)

    # Per-position decision counts
    pos_counts = Counter(d['_pos'] for d in all_decisions)

    return {
        'match_id': match_id,
        'team_side': team_side,
        'result': result,
        'our_score': our_score,
        'opp_score': opp_score,
        'final_clock': final_clock,
        'duration': duration,
        'first_clock': first_snap.get('clock', 0),
        'total_decisions': len(all_decisions),
        'total_errors': error_count,
        'score_transitions': score_transitions,
        'command_distribution': dict(cmd_counts.most_common(10)),
        'rush_events': rush_count,
        'stage_distribution': dict(stage_counts),
        'our_possession_pct': our_poss,
        'avg_latency_ms': round(avg_latency, 1) if avg_latency else None,
        'profile_versions': list(profile_versions),
        'pos_decision_counts': dict(pos_counts),
    }

if __name__ == '__main__':
    idx_file = '/Users/aleksandr/Local/EPAM/aws_football_challenge/.worktrees/final-agent-s3/.analysis/match_index.json'
    with open(idx_file) as f:
        index = json.load(f)

    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    results = []

    print(f"Analyzing top {n} matches...")
    for i, (uuid, info) in enumerate(list(index.items())[:n]):
        print(f"\n[{i+1}/{n}] {uuid[:8]}... ({info['last_event_utc'][:19]})")
        summary = analyze_match(uuid, info['seats'])
        if summary:
            print(f"  Result: {summary['result']} ({summary['our_score']}:{summary['opp_score']})")
            print(f"  Clock: {summary['first_clock']:.0f}s - {summary['final_clock']:.0f}s / {summary['duration']:.0f}s")
            print(f"  Decisions: {summary['total_decisions']}  Errors: {summary['total_errors']}")
            print(f"  Rush events: {summary['rush_events']}  Our poss: {summary['our_possession_pct']}%")
            top_cmds = list(summary['command_distribution'].items())[:5]
            print(f"  Top cmds: {top_cmds}")
            print(f"  Score transitions: {summary['score_transitions']}")
            results.append(summary)
        else:
            print(f"  No decisions found")

    out_file = '/Users/aleksandr/Local/EPAM/aws_football_challenge/.worktrees/final-agent-s3/.analysis/match_summaries.json'
    with open(out_file, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved to .analysis/match_summaries.json")
