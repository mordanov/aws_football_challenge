"""
Validate that pass_margin=2.0 enables passes blocked by pass_margin=3.0.
Tests the specific scenario observed in matches: opponent 2.5m from receiver.
"""
import sys, os
sys.path.insert(0, '/Users/aleksandr/Local/EPAM/aws_football_challenge/.worktrees/final-agent-s3/final_agent')

from agent.model import Player, Settings, Style, Snapshot, DEFAULT_STYLE
from agent.tactics import carrier_options, decide

def make_player(team, pid, x, y, vx=0.0, vy=0.0, stamina=0.8):
    return Player(team, pid, x, y, vx, vy, stamina)

def make_snapshot(carrier_team, carrier_pid, carrier_x, carrier_y,
                  receiver_x, receiver_y, opp_marker_x, opp_marker_y):
    """
    Our carrier has the ball at carrier_x.
    Our teammate (FWD) is at receiver_x — potential pass target.
    Opponent marker is near the receiver at opp_marker_x.
    """
    # Our team = 0, direction = 1
    team = 0
    players = (
        make_player(0, 0, -50, 0),          # our GK
        make_player(0, 1, -25, 0),          # our DEF
        make_player(0, 2, carrier_x, carrier_y),   # our MID (carrier)
        make_player(0, 3, receiver_x, receiver_y),  # our FWD (potential receiver)
        make_player(0, 4, 20, 10),          # our FWD2
        make_player(1, 0, 50, 0),           # opp GK
        make_player(1, 1, 10, 5),           # opp DEF1
        make_player(1, 2, 5, -5),           # opp DEF2
        make_player(1, 3, opp_marker_x, opp_marker_y),  # opp marking our FWD receiver
        make_player(1, 4, -5, 10),          # opp FWD
    )
    return Snapshot(
        team=0,
        tick=10,
        clock=45.0,
        duration=120.0,
        players=players,
        ball=(carrier_x, carrier_y),
        velocity=(0.0, 0.0),
        owner=(0, carrier_pid),
        free=False,
        score=(0, 0),
    )


def count_pass_options(snap, carrier_pid, pass_margin):
    """Count how many safe pass options exist for the given pass_margin."""
    settings = Settings(pass_margin=pass_margin, shot_range=32.0, press_radius=22.0, support_width=15.0)
    carrier = next(p for p in snap.players if p.team == snap.team and p.id == carrier_pid)
    opts = carrier_options(snap, carrier, settings, DEFAULT_STYLE)
    passes = [o for o in opts if o[1].get('commandType') == 'PASS']
    return passes


print("=== pass_margin validation ===\n")

# Scenario: Our MID at x=5 (center-right), FWD at x=25 (forward position, 30m from goal)
# Opponent marks FWD at 2.5m distance
scenarios = [
    {"marker_dist": 2.0, "label": "marker at 2.0m from FWD"},
    {"marker_dist": 2.5, "label": "marker at 2.5m from FWD"},
    {"marker_dist": 3.0, "label": "marker at 3.0m from FWD"},
    {"marker_dist": 3.5, "label": "marker at 3.5m from FWD"},
    {"marker_dist": 4.0, "label": "marker at 4.0m from FWD"},
]

receiver_x, receiver_y = 25.0, 0.0

print(f"Carrier: MID at (5, 0), FWD receiver at (25, 0)")
print(f"{'Scenario':<35} | PM=3.0 passes | PM=2.0 passes | Delta")
print("-" * 70)

for sc in scenarios:
    d = sc["marker_dist"]
    snap = make_snapshot(0, 2, 5.0, 0.0, receiver_x, receiver_y, receiver_x, receiver_y + d)

    passes_30 = count_pass_options(snap, 2, 3.0)
    passes_20 = count_pass_options(snap, 2, 2.0)

    unlocked = len(passes_20) - len(passes_30)
    indicator = "✓ NEW PASS" if unlocked > 0 else "  same"
    print(f"{sc['label']:<35} | {len(passes_30):13d} | {len(passes_20):13d} | {indicator}")

# Specific: show what command is chosen for tight marking (2.5m)
print("\n--- Best action chosen at 2.5m marker gap ---")
snap_tight = make_snapshot(0, 2, 5.0, 0.0, 25.0, 0.0, 25.0, 2.5)
carrier = next(p for p in snap_tight.players if p.team == 0 and p.id == 2)

settings_30 = Settings(pass_margin=3.0)
settings_20 = Settings(pass_margin=2.0)

opts_30 = carrier_options(snap_tight, carrier, settings_30, DEFAULT_STYLE)
opts_20 = carrier_options(snap_tight, carrier, settings_20, DEFAULT_STYLE)

best_30 = opts_30[0] if opts_30 else None
best_20 = opts_20[0] if opts_20 else None

if best_30:
    print(f"  PM=3.0: {best_30[1]['commandType']:15s} score={best_30[0]:.1f} reason='{best_30[2][:50]}'")
if best_20:
    print(f"  PM=2.0: {best_20[1]['commandType']:15s} score={best_20[0]:.1f} reason='{best_20[2][:50]}'")
