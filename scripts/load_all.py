"""Load all research data into the warehouse (one command).

Runs, in order:
  1. NSE bhavcopy EOD bars (technical)
  2. Fundamentals via yfinance (ratios + results)
  3. BSE announcements into filings (news / RAG)

Usage (repo root, venv active):
  python -m scripts.load_all
  python -m scripts.load_all --days 30
  python -m scripts.load_all --symbols RELIANCE,TCS,INFY
  python -m scripts.load_all --skip-bhavcopy
  python -m scripts.load_all --continue-on-error
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LOG = logging.getLogger("load_all")


def setup_logging(verbose: bool = False) -> Path:
    """Console + file log under logs/load_all_YYYYMMDD_HHMMSS.log"""
    log_dir = ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"load_all_{stamp}.log"

    level = logging.DEBUG if verbose else logging.INFO
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(level)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    return log_path


def run(step_no: int, total: int, label: str, module_args: list[str]) -> int:
    cmd = [sys.executable, "-m", *module_args]
    LOG.info("-" * 60)
    LOG.info("STEP %s/%s START — %s", step_no, total, label)
    LOG.info("Command: %s", " ".join(cmd))
    LOG.info("Working directory: %s", ROOT)
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            # inherit stdout/stderr so child print() shows live
        )
        code = proc.returncode
    except Exception:
        LOG.exception("STEP %s/%s crashed before exit — %s", step_no, total, label)
        return 1

    elapsed = time.perf_counter() - t0
    if code == 0:
        LOG.info("STEP %s/%s OK — %s (%.1fs)", step_no, total, label, elapsed)
    else:
        LOG.error(
            "STEP %s/%s FAILED — %s (exit=%s, %.1fs)",
            step_no, total, label, code, elapsed,
        )
    return code


def main() -> int:
    p = argparse.ArgumentParser(description="Load bhavcopy + fundamentals + announcements")
    p.add_argument("--days", type=int, default=30, help="Bhavcopy weekday backfill (default 30)")
    p.add_argument("--symbols", default="", help="Comma-separated symbols for fundamentals & announcements")
    p.add_argument("--skip-bhavcopy", action="store_true")
    p.add_argument("--skip-fundamentals", action="store_true")
    p.add_argument("--skip-announcements", action="store_true")
    p.add_argument("--continue-on-error", action="store_true", help="Run all steps even if one fails")
    p.add_argument("-v", "--verbose", action="store_true", help="DEBUG logging")
    args = p.parse_args()

    log_path = setup_logging(verbose=args.verbose)
    started = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    plan = []
    if not args.skip_bhavcopy:
        plan.append(("NSE bhavcopy (EOD prices)", [
            "scripts.refresh_bhavcopy", "--days", str(args.days),
        ]))
    if not args.skip_fundamentals:
        cmd = ["scripts.load_fundamentals"]
        if args.symbols:
            cmd += ["--symbols", args.symbols]
        plan.append(("Fundamentals (yfinance → DB)", cmd))
    if not args.skip_announcements:
        cmd = ["scripts.load_announcements"]
        if args.symbols:
            cmd += ["--symbols", args.symbols]
        plan.append(("Announcements (BSE → filings)", cmd))

    LOG.info("=" * 60)
    LOG.info("load_all started at %s", started)
    LOG.info("Log file: %s", log_path)
    LOG.info("Python: %s", sys.executable)
    LOG.info("Plan: %s step(s)", len(plan))
    for i, (label, _) in enumerate(plan, 1):
        LOG.info("  %s. %s", i, label)
    if args.symbols:
        LOG.info("Symbols filter: %s", args.symbols)
    if args.skip_bhavcopy or args.skip_fundamentals or args.skip_announcements:
        LOG.info(
            "Skips: bhavcopy=%s fundamentals=%s announcements=%s",
            args.skip_bhavcopy, args.skip_fundamentals, args.skip_announcements,
        )
    LOG.info("=" * 60)

    if not plan:
        LOG.warning("Nothing to run (all steps skipped).")
        return 0

    codes: list[tuple[str, int]] = []
    total = len(plan)

    for i, (label, module_args) in enumerate(plan, 1):
        code = run(i, total, label, module_args)
        codes.append((label, code))
        if code != 0 and not args.continue_on_error:
            LOG.error("Stopping after failure (use --continue-on-error to run remaining steps).")
            break

    LOG.info("=" * 60)
    LOG.info("SUMMARY")
    failed = 0
    for label, code in codes:
        status = "OK" if code == 0 else f"FAILED ({code})"
        if code != 0:
            failed += 1
        LOG.info("  [%s] %s", status, label)
    skipped = total - len(codes)
    if skipped:
        LOG.info("  (not run: %s step(s) after stop)", skipped)

    if failed:
        LOG.error("Finished with %s failed step(s). See log: %s", failed, log_path)
        return 1

    LOG.info("All planned steps finished successfully.")
    LOG.info("Tip: DATA_PROVIDER=db (or auto) prefers the warehouse for ratios.")
    LOG.info("Full log: %s", log_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
