"""
Headless CLI launcher for StockEdge Options.
Runs analysis and prints report directly to terminal without web browser.

Usage:
  python run_headless.py [TICKER] [--execute]

Examples:
  python run_headless.py RELIANCE
  python run_headless.py TCS --execute
"""

import argparse
import sys
import threading
import futures_engine


def main():
    parser = argparse.ArgumentParser(description="StockEdge Options CLI")
    parser.add_argument("ticker", nargs="?", default=None, help="NSE stock ticker (e.g. RELIANCE)")
    parser.add_argument("--execute", action="store_true", help="Execute futures trade if recommendation is LONG/SHORT")
    args = parser.parse_args()

    settings = futures_engine.load_settings()
    if args.ticker:
        settings["ticker"] = args.ticker.upper()

    print("=" * 60)
    print(f" StockEdge Options CLI — {settings['ticker']} Futures Analysis")
    print("=" * 60)

    stop_event = threading.Event()

    try:
        if args.execute:
            print("[cli] Running full analysis + execute mode...")
            futures_engine.run(stop_event=stop_event, settings_path="settings.json", log=print)
        else:
            print("[cli] Running analyze-only mode (analyze first, you decide)...")
            res = futures_engine.analyze_only(settings_path="settings.json", log=print)
            score_data = res.get("trade_score", {})
            print("\n" + "=" * 60)
            print(f" Ticker:           {res.get('ticker')}")
            print(f" Composite Score:  {score_data.get('score')}/100")
            print(f" Grade:            {score_data.get('grade')}")
            print(f" Signal:           {score_data.get('signal')}")
            print(f" AI Sentiment:     {res.get('ai_sentiment')}")
            print(f" Final Direction:  {res.get('direction')}")
            print(f" Report Path:      {res.get('report_path')}")
            print("=" * 60 + "\n")
    except KeyboardInterrupt:
        print("\n[cli] Interrupted by user. Exiting...")
        stop_event.set()


if __name__ == "__main__":
    main()
