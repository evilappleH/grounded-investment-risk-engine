from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path

from .config import load_rules
from .engine import PriceWatchEngine
from .moomoo_quotes import MoomooQuoteProvider
from .persistence import JsonStateStore, JsonlEventSink
from .review import TriggeredResearchReviewer, persist_review


ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(description="One read-only price-watch cycle; never submits orders")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state", type=Path, default=ROOT / "data_cache" / "price_watch" / "state.json")
    parser.add_argument("--events", type=Path, default=ROOT / "data_cache" / "price_watch" / "events.jsonl")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11111)
    parser.add_argument("--max-quote-age-seconds", type=int, default=300)
    parser.add_argument("--skip-agent-review", action="store_true")
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    rules = load_rules(args.config)
    tickers = tuple(dict.fromkeys(rule.ticker for rule in rules if rule.enabled))
    with MoomooQuoteProvider(args.host, args.port) as provider:
        quotes = provider.snapshots(tickers)
    store = JsonStateStore(args.state)
    events, states = PriceWatchEngine(max_quote_age_seconds=args.max_quote_age_seconds).evaluate(rules, quotes, store.load(), now=datetime.now(timezone.utc))
    store.save(states)
    sink = JsonlEventSink(args.events)
    reviewer = TriggeredResearchReviewer(ROOT, model=args.model)
    results = []
    for event in events:
        sink.append(event)
        item = {"event": asdict(event), "review": "SKIPPED"}
        if not args.skip_agent_review:
            review = reviewer.review(event)
            item["review"] = review["status"]
            item["review_path"] = str(persist_review(review, args.events.parent / "reviews"))
        results.append(item)
    print(json.dumps({"status": "OK", "execution": "NO_ORDER", "quotes": {key: asdict(value) for key, value in quotes.items()}, "triggered": results}, default=str, indent=2))


if __name__ == "__main__":
    main()
