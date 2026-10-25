"""Discover recent matches from CloudWatch log streams."""
import subprocess, os, json, re, sys
from datetime import datetime, timedelta, timezone

CREDS_FILE = '/Users/aleksandr/Local/EPAM/aws_football_challenge/aws_creds.txt'
DIAG_UUID = "6dc3a574-299e-44e4-81aa-6eb36c5776ef"
UUID_RE = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')

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

hours = int(sys.argv[1]) if len(sys.argv) > 1 else 24
now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
start_ms = now_ms - hours * 3600 * 1000

print(f"Searching last {hours}h for matches...")
matches = {}

for pos, group in CW_GROUPS.items():
    token = None
    page = 0
    while True:
        cmd = ['aws', 'logs', 'describe-log-streams',
               '--log-group-name', group,
               '--order-by', 'LastEventTime',
               '--descending',
               '--limit', '50',
               '--region', 'us-east-1']
        if token:
            cmd += ['--next-token', token]
        r = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  Error pos{pos}: {r.stderr[:200]}")
            break
        data = json.loads(r.stdout)
        streams = data.get('logStreams', [])
        for s in streams:
            name = s.get('logStreamName', '')
            last_event = s.get('lastEventTimestamp', 0)
            if last_event < start_ms:
                continue
            m = UUID_RE.search(name)
            if not m:
                continue
            uuid = m.group(0)
            if uuid == DIAG_UUID:
                continue
            team_m = re.search(r'team(\d)', name)
            pos_m = re.search(r'pos(\d)', name)
            if uuid not in matches:
                matches[uuid] = {'teams': set(), 'seats': {}, 'last_event': 0, 'streams': []}
            matches[uuid]['last_event'] = max(matches[uuid]['last_event'], last_event)
            matches[uuid]['streams'].append({'name': name, 'pos': pos, 'group': group})
            if team_m:
                matches[uuid]['teams'].add(team_m.group(1))
            if pos_m and team_m:
                key = f"team{team_m.group(1)}_pos{pos_m.group(1)}"
                matches[uuid]['seats'][key] = {'stream': name, 'group': group}
        token = data.get('nextToken')
        page += 1
        if not token or page > 5:
            break

# Sort by last_event descending
sorted_matches = sorted(matches.items(), key=lambda x: -x[1]['last_event'])
print(f"\n=== {len(sorted_matches)} matches found (last {hours}h) ===")
for uuid, info in sorted_matches:
    ts = datetime.fromtimestamp(info['last_event']/1000, tz=timezone.utc)
    seats = len(info['seats'])
    print(f"\n{uuid}")
    print(f"  Last event: {ts.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"  Teams: {sorted(info['teams'])}")
    print(f"  Seats covered: {seats}/10  {sorted(info['seats'].keys())}")

# Save index
index = {}
for uuid, info in sorted_matches:
    index[uuid] = {
        'last_event_ms': info['last_event'],
        'last_event_utc': datetime.fromtimestamp(info['last_event']/1000, tz=timezone.utc).isoformat(),
        'teams': sorted(info['teams']),
        'seats': {k: v for k, v in info['seats'].items()},
        'stream_count': len(info['streams']),
    }
with open('/Users/aleksandr/Local/EPAM/aws_football_challenge/.worktrees/final-agent-s3/.analysis/match_index.json', 'w') as f:
    json.dump(index, f, indent=2)
print(f"\nIndex saved to .analysis/match_index.json")
