"""Thin wrapper around grpc.server for embedding GEPAService in a process."""

from __future__ import annotations

import logging
from concurrent import futures

import grpc

from gepa_rpc.generated import gepa_pb2_grpc as pb_grpc
from gepa_rpc.servicer import DEFAULT_RUNS_DIR, GEPAServicer

logger = logging.getLogger(__name__)


def build_server(
    port: int,
    runs_dir: str = DEFAULT_RUNS_DIR,
    max_workers: int = 16,
) -> grpc.Server:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    pb_grpc.add_GEPAServiceServicer_to_server(GEPAServicer(runs_dir=runs_dir), server)
    server.add_insecure_port(f"[::]:{port}")
    return server


def serve(port: int, runs_dir: str = DEFAULT_RUNS_DIR, max_workers: int = 16) -> None:
    server = build_server(port=port, runs_dir=runs_dir, max_workers=max_workers)
    server.start()
    logger.info("gepa-rpc listening on :%d (runs_dir=%s)", port, runs_dir)
    server.wait_for_termination()
