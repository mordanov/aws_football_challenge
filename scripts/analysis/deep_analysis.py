"""Deep analysis of early goals and tactical patterns."""
import subprocess, os, json, sys
from datetime import datetime, timezone
from collections import Counter, defaultdict

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
    for _ in range(5):
        cmd = ['aws', 'logs', 'get-log-events', '--log-group-name', group,
               '--log-stream-name', stream_name, '--limit', '1000', '--region', 'us-east-1']
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

def fetch_all_decisions(uuid, seats):
    """Fetch all decisions for a match, de-duplicated by tick+pos."""
    by_pos = {}
    for seat_key, seat_info in seats.items():
        pos = int(seat_key.split('_pos')[1])
        raw = fetch_stream(seat_info['group'], seat_info['stream'])
        decisions = []
        for ev in raw:
            try:
                msg = json.loads(ev['message'])
                if msg.get('type') == 'decision':
                    msg['_pos'] = pos
                    decisions.append(msg)
            except Exception:
                pass
        by_pos[pos] = decisions
    return by_pos

def unique_score_transitions(decisions_by_pos):
    """Extract unique score transitions from any position's stream."""
    # Use pos 0 (GK) as canonical since it sees every tick
    canonical = decisions_by_pos.get(0, [])
    if not canonical:
        for p in sorted(decisions_by_pos.keys()):
            if decisions_by_pos[p]:
                canonical = decisions_by_pos[p]
                break

    canonical.sort(key=lambda d: (d.get('snapshot', {}).get('clock', 0), d.get('tick', 0)))

    transitions = []
    prev_score = None
    for d in canonical:
        snap = d.get('snapshot', {})
        score = tuple(snap.get('score', [0, 0]))
        if score != prev_score:
            transitions.append({
                'clock': snap.get('clock', 0),
                'score': list(score),
                'tick': d.get('tick'),
            })
            prev_score = score
    return transitions

def analyze_early_period(decisions_by_pos, team_side, clock_window=30):
    """Analyze decisions in early match phase."""
    team_idx = team_side or 0
    by_clock = defaultdict(list)
    for pos, decs in decisions_by_pos.items():
        for d in decs:
            clock = d.get('snapshot', {}).get('clock', 999)
            if clock <= clock_window:
                by_clock[round(clock, 1)].append((pos, d))

    stats = {
        'decisions_per_pos': {pos: 0 for pos in range(5)},
        'commands_early': Counter(),
        'rush_active_ticks': 0,
        'rush_suppressed_ticks': 0,
        'poss_ours': 0,
        'poss_opp': 0,
        'poss_free': 0,
    }

    seen_clocks_per_pos = defaultdict(set)

    for clock, events in sorted(by_clock.items()):
        for pos, d in events:
            if clock not in seen_clocks_per_pos[pos]:
                seen_clocks_per_pos[pos].add(clock)
                stats['decisions_per_pos'][pos] = stats['decisions_per_pos'].get(pos, 0) + 1
                cmd = d.get('command', {}).get('commandType')
                if cmd:
                    stats['commands_early'][cmd] += 1

                rush = d.get('rush', 0)
                if rush == 1:
                    stats['rush_active_ticks'] += 1
                else:
                    stats['rush_suppressed_ticks'] += 1

                snap = d.get('snapshot', {})
                owner = snap.get('owner')
                if owner is None:
                    stats['poss_free'] += 1
                elif (owner[0] if owner else None) == team_idx:
                    stats['poss_ours'] += 1
                else:
                    stats['poss_opp'] += 1

    return stats

def analyze_ball_in_own_half_rush(decisions_by_pos, team_side):
    """Check if rush suppression worked: were there rush events when ball was in own half?"""
    team_idx = team_side or 0
    rush_with_ball_behind = []
    rush_suppressed_ball_behind = []

    canonical = decisions_by_pos.get(0, [])
    canonical.sort(key=lambda d: d.get('snapshot', {}).get('clock', 0))

    for d in canonical:
        snap = d.get('snapshot', {})
        clock = snap.get('clock', 0)
        if clock > 20:  # only early match
            break

        ball = snap.get('ball', {}).get('position', {}) if isinstance(snap.get('ball'), dict) else {}
        ball_x = ball.get('x', 0) if isinstance(ball, dict) else (snap.get('ball', [0,0])[0] if isinstance(snap.get('ball'), (list, tuple)) else 0)
        direction = snap.get('direction', 1)
        rush = d.get('rush', 0)

        # ball_in_front check: ball_x * direction >= -10 means ball is in front / midfield
        ball_in_front = ball_x * direction >= -10

        if rush == 1 and not ball_in_front:
            rush_with_ball_behind.append({'clock': clock, 'ball_x': ball_x, 'direction': direction})
        elif rush == 0 and not ball_in_front:
            rush_suppressed_ball_behind.append({'clock': clock, 'ball_x': ball_x, 'direction': direction})

    return rush_with_ball_behind, rush_suppressed_ball_behind

idx_file = '/Users/aleksandr/Local/EPAM/aws_football_challenge/.worktrees/final-agent-s3/.analysis/match_index.json'
with open(idx_file) as f:
    index = json.load(f)

summaries_file = '/Users/aleksandr/Local/EPAM/aws_football_challenge/.worktrees/final-agent-s3/.analysis/match_summaries.json'
with open(summaries_file) as f:
    summaries = json.load(f)

print("=== DEEP ANALYSIS: Top 5 Matches ===\n")

full_results = []

for i, summary in enumerate(summaries):
    uuid = summary['match_id']
    info = index[uuid]
    team_side = summary['team_side']
    team_idx = team_side or 0

    print(f"\n--- Match {i+1}: {uuid[:8]} ---")
    print(f"Result: {summary['result']} ({summary['our_score']}:{summary['opp_score']})")

    # Fetch for deep analysis
    decs_by_pos = fetch_all_decisions(uuid, info['seats'])

    # Unique score transitions
    transitions = unique_score_transitions(decs_by_pos)
    print(f"Score transitions (unique):")
    for t in transitions:
        print(f"  clock={t['clock']:.1f}s  {t['score']}")

    # Identify first conceded goal
    first_conceded = None
    for t in transitions[1:]:  # skip initial 0:0
        score = t['score']
        if score[1-team_idx] > (transitions[transitions.index(t)-1]['score'][1-team_idx] if transitions.index(t) > 0 else 0):
            first_conceded = t
            break

    if first_conceded:
        print(f"First goal conceded: clock={first_conceded['clock']:.1f}s")
        early = first_conceded['clock'] < 20
        print(f"  Early goal (<20s): {'YES' if early else 'no'}")

    # Early period analysis
    early_stats = analyze_early_period(decs_by_pos, team_side, clock_window=25)
    print(f"\nEarly match (0-25s) stats:")
    print(f"  Rush active: {early_stats['rush_active_ticks']} ticks")
    print(f"  Rush suppressed: {early_stats['rush_suppressed_ticks']} ticks")
    print(f"  Possession - ours: {early_stats['poss_ours']}, opp: {early_stats['poss_opp']}, free: {early_stats['poss_free']}")
    print(f"  Early commands: {dict(early_stats['commands_early'].most_common(5))}")

    # Check rush-when-ball-behind
    rush_bad, rush_suppressed = analyze_ball_in_own_half_rush(decs_by_pos, team_side)
    if rush_bad:
        print(f"  WARNING: Rush active with ball in own half: {len(rush_bad)} instances")
        for rb in rush_bad[:3]:
            print(f"    clock={rb['clock']:.1f}s ball_x={rb['ball_x']:.1f} dir={rb['direction']}")
    else:
        print(f"  Rush suppression (ball in own half): OK, no bad rush instances")

    full_results.append({
        'match_id': uuid,
        'result': summary['result'],
        'score': f"{summary['our_score']}:{summary['opp_score']}",
        'first_conceded_clock': first_conceded['clock'] if first_conceded else None,
        'early_goal': (first_conceded['clock'] < 20) if first_conceded else False,
        'rush_active_early': early_stats['rush_active_ticks'],
        'rush_suppressed_early': early_stats['rush_suppressed_ticks'],
        'poss_early': early_stats,
        'rush_bad_instances': rush_bad,
    })

# Summary analysis
print("\n\n=== SUMMARY ===")
results_count = Counter(r['result'] for r in full_results)
print(f"Results: {dict(results_count)}")

early_goals = sum(1 for r in full_results if r.get('early_goal'))
print(f"Early goals conceded (<20s): {early_goals}/{len(full_results)} matches")

for r in full_results:
    conc = r['first_conceded_clock']
    print(f"  {r['match_id'][:8]}: {r['result']} {r['score']}  first_goal={conc:.1f}s" if conc else f"  {r['match_id'][:8]}: {r['result']} {r['score']}  no_goal_conceded")

with open('/Users/aleksandr/Local/EPAM/aws_football_challenge/.worktrees/final-agent-s3/.analysis/deep_results.json', 'w') as f:
    json.dump(full_results, f, indent=2, default=str)
print("\nSaved to .analysis/deep_results.json")
