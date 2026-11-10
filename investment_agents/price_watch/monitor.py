from __future__ import annotations

import argparse
import subprocess
import sys
import time


def main() -> None:
    parser = argparse.ArgumentParser(description="Continuously run the read-only price monitor")
    parser.add_argument("--config", required=True)
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--cycles", type=int, default=0, help="0 means run until interrupted")
    parser.add_argument("--skip-agent-review", action="store_true")
    args = parser.parse_args()
    if args.interval_seconds < 30:
        raise SystemExit("interval-seconds must be at least 30")
    cycle = 0
    while args.cycles == 0 or cycle < args.cycles:
        command = [sys.executable, "-m", "investment_agents.price_watch.run_once", "--config", args.config]
        if args.skip_agent_review:
            command.append("--skip-agent-review")
        completed = subprocess.run(command, check=False)
        cycle += 1
        if completed.returncode != 0:
            print("price-watch cycle failed closed; no order was attempted", file=sys.stderr)
        if args.cycles == 0 or cycle < args.cycles:
            time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
