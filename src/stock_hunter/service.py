import time
import argparse
import sys
from .pipeline import run_pipeline
from .logger import step, info, warning

def run_service(interval_minutes=60, skip_form4=False, reset_financials=False, resume_llm=False, skip_8k=False):
    step(f"Starting background daemon service (interval={interval_minutes} mins)")
    try:
        while True:
            run_pipeline(
                skip_form4=skip_form4,
                reset_financials=reset_financials,
                resume_llm=resume_llm,
                skip_8k=skip_8k,
            )
            info(f"Sleeping for {interval_minutes} minutes before next cycle")
            time.sleep(interval_minutes * 60)
    except KeyboardInterrupt:
        warning("Service stopped by user")
        sys.exit(130)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Drawdown Analyzer Pipeline Service")
    parser.add_argument("--daemon", action="store_true", help="Run continuously in background service mode")
    parser.add_argument("--interval", type=int, default=60, help="Interval in minutes for background daemon (default: 60)")
    parser.add_argument("--skip-form4", action="store_true", help="Skip SEC Form 4 insider ingestion")
    parser.add_argument("--skip-8k", action="store_true", help="Skip SEC 8-K debt/bankruptcy event ingestion")
    parser.add_argument("--reset-financials", action="store_true", help="Clear sec_financials before reseeding from SEC")
    parser.add_argument(
        "--resume-llm",
        action="store_true",
        help="Skip SEC fetch/reseed and resume from the existing LLM narrative scoring pass",
    )
    args = parser.parse_args()

    if args.resume_llm and args.reset_financials:
        warning("--resume-llm cannot be combined with --reset-financials")
        sys.exit(2)

    if args.daemon:
        try:
            run_service(
                args.interval,
                skip_form4=args.skip_form4,
                reset_financials=args.reset_financials,
                resume_llm=args.resume_llm,
                skip_8k=args.skip_8k,
            )
        except KeyboardInterrupt:
            warning("Service stopped by user")
            sys.exit(130)
    else:
        try:
            step("Running one-time pipeline execution")
            run_pipeline(
                skip_form4=args.skip_form4,
                reset_financials=args.reset_financials,
                resume_llm=args.resume_llm,
                skip_8k=args.skip_8k,
            )
        except KeyboardInterrupt:
            warning("Pipeline interrupted by user")
            sys.exit(130)
