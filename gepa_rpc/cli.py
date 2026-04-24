"""Entry point for the `gepa-rpc` console script."""

from __future__ import annotations

import argparse
import logging
import sys

from gepa_rpc.server import serve
from gepa_rpc.servicer import DEFAULT_RUNS_DIR


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gepa-rpc", description="Launch the GEPA gRPC server.")
    parser.add_argument("--port", type=int, default=50051)
    parser.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR, help="Directory for per-run checkpoints.")
    parser.add_argument("--max-workers", type=int, default=16)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    try:
        serve(port=args.port, runs_dir=args.runs_dir, max_workers=args.max_workers)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
