#!/usr/bin/env python3
"""
PageScore HQ — Nightly Pipeline Scheduler

Rotates through city/category combos automatically.
Tracks which combo to run next via a simple state file.
Tier 1 (high-value trades) runs first, then Tier 2.
"""

import json
import os
import sys
import subprocess
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(SCRIPT_DIR, "scheduler_state.json")

# ── Target combos ──────────────────────────────────────────
CITIES = ["Dallas", "Houston", "Austin"]

TIER_1 = ["HVAC", "roofer", "plumber", "electrician"]
TIER_2 = ["pest control", "landscaper", "auto repair"]

# Build rotation: all Tier 1 combos first, then Tier 2
COMBOS = []
for category in TIER_1:
    for city in CITIES:
        COMBOS.append({"city": city, "category": category, "tier": 1})
for category in TIER_2:
    for city in CITIES:
        COMBOS.append({"city": city, "category": category, "tier": 2})

# Total: 21 combos (12 Tier 1 + 9 Tier 2) = 21 nights to complete full cycle


def load_state():
    """Load scheduler state (which combo index we're on)."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"index": 0, "history": []}


def save_state(state):
    """Save scheduler state."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def run_pipeline(city, category):
    """Run main.py for a given city/category."""
    cmd = [
        sys.executable, os.path.join(SCRIPT_DIR, "main.py"),
        "--city", city,
        "--category", category,
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=SCRIPT_DIR,
        timeout=1800,  # 30 min max per run
    )
    return result


def main():
    state = load_state()
    idx = state["index"] % len(COMBOS)
    combo = COMBOS[idx]

    city = combo["city"]
    category = combo["category"]
    tier = combo["tier"]

    print(f"{'='*60}")
    print(f"PageScore Scheduler — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Running combo {idx+1}/{len(COMBOS)}: {category} in {city} (Tier {tier})")
    print(f"{'='*60}")

    try:
        result = run_pipeline(city, category)

        # Log output
        log_entry = {
            "date": datetime.now().isoformat(),
            "city": city,
            "category": category,
            "tier": tier,
            "exit_code": result.returncode,
        }

        if result.returncode == 0:
            print(f"✓ Pipeline completed successfully")
            print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
        else:
            print(f"✗ Pipeline failed (exit code {result.returncode})")
            print(result.stderr[-500:] if len(result.stderr) > 500 else result.stderr)

        # Advance to next combo
        state["index"] = idx + 1
        state["history"] = state.get("history", [])[-30:]  # keep last 30 runs
        state["history"].append(log_entry)
        save_state(state)

    except subprocess.TimeoutExpired:
        print(f"✗ Pipeline timed out after 30 minutes")
        state["index"] = idx + 1
        save_state(state)

    except Exception as e:
        print(f"✗ Scheduler error: {e}")


if __name__ == "__main__":
    main()
