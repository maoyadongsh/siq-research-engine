#!/usr/bin/env python3
from __future__ import annotations

import argparse


def classify(ticker: str, company_name: str = "", sic: str = "") -> str:
    text = f"{ticker} {company_name} {sic}".lower()
    if "bank" in text:
        return "bank"
    if "insurance" in text:
        return "insurance"
    if "semiconductor" in text or ticker.upper() in {"NVDA", "AMD", "INTC", "TSM"}:
        return "semiconductor"
    if "apple" in text or ticker.upper() == "AAPL":
        return "consumer_hardware"
    return "general"


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify a SEC issuer into a SIQ industry profile.")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--company-name", default="")
    parser.add_argument("--sic", default="")
    args = parser.parse_args()
    print(classify(args.ticker, args.company_name, args.sic))


if __name__ == "__main__":
    main()
