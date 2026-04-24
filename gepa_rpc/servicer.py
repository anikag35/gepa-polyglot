"""gRPC servicer for GEPAService.

Threading model for RunOptimization:
- handler thread: reads first message (StartRequest), then drains the outbound
  queue and yields ServerMessages back to the client.
- reader thread: pulls subsequent ClientMessages and resolves adapter futures.
- runner thread: invokes gepa.optimize(adapter=RemoteAdapter(...)) synchronously.

Checkpointing: gepa.optimize persists state under run_dir. Re-issuing
RunOptimization with the same run_id on a fresh stream will resume from disk.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
from typing import Any

import grpc

import gepa

from gepa_rpc.adapter import RemoteAdapter
from gepa_rpc.conversions import RemoteExample
from gepa_rpc.generated import gepa_pb2 as pb
from gepa_rpc.generated import gepa_pb2_grpc as pb_grpc

logger = logging.getLogger(__name__)

# TODO: make configurable via StartRequest.reflection_lm; hardcoded per spec.
HARDCODED_REFLECTION_LM = "gpt-5"

DEFAULT_RUNS_DIR = os.environ.get("GEPA_RPC_RUNS_DIR", "./runs")


class _ProgressCallback:
    """Bridges gepa callback events to ProgressUpdate messages on the stream."""

    def __init__(
        self,
        outbound: "queue.Queue[pb.ServerMessage | None]",
        max_metric_calls: int,
        run_status: dict[str, Any],
    ):
        self._outbound = outbound
        self._max_metric_calls = max_metric_calls
        self._run_status = run_status
        self._best_score = float("-inf")
        self._best_candidate: dict[str, str] = {}

    def on_budget_updated(self, event: dict[str, Any]) -> None:
        used = int(event["metric_calls_used"])
        self._run_status["metric_calls_used"] = used
        self._emit(used)

    def on_valset_evaluated(self, event: dict[str, Any]) -> None:
        avg = float(event["average_score"])
        if avg > self._best_score:
            self._best_score = avg
            self._best_candidate = dict(event["candidate"])
            self._emit(self._run_status.get("metric_calls_used", 0))

    def _emit(self, metric_calls_used: int) -> None:
        update = pb.ProgressUpdate(
            metric_calls_used=metric_calls_used,
            max_metric_calls=self._max_metric_calls,
            best_score=self._best_score if self._best_score != float("-inf") else 0.0,
            best_candidate=self._best_candidate,
        )
        self._outbound.put(pb.ServerMessage(progress_update=update))


class GEPAServicer(pb_grpc.GEPAServiceServicer):
    def __init__(self, runs_dir: str = DEFAULT_RUNS_DIR):
        self._runs_dir = runs_dir
        self._runs: dict[str, dict[str, Any]] = {}
        self._runs_lock = threading.Lock()

    # ------------------------------------------------------------------ status
    def GetStatus(self, request: pb.StatusRequest, context: grpc.ServicerContext) -> pb.StatusResponse:
        with self._runs_lock:
            entry = self._runs.get(request.run_id)
        if entry is None:
            return pb.StatusResponse(run_id=request.run_id, status=pb.StatusResponse.UNKNOWN)
        status_map = {
            "running": pb.StatusResponse.RUNNING,
            "complete": pb.StatusResponse.COMPLETE,
            "failed": pb.StatusResponse.FAILED,
        }
        return pb.StatusResponse(
            run_id=request.run_id,
            status=status_map.get(entry["status"], pb.StatusResponse.UNKNOWN),
            message=entry.get("message", ""),
            metric_calls_used=entry.get("metric_calls_used", 0),
        )

    # ----------------------------------------------------------- optimization
    def RunOptimization(self, request_iterator, context: grpc.ServicerContext):
        try:
            first = next(request_iterator)
        except StopIteration:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("client closed stream before sending start_request")
            return

        if not first.HasField("start_request"):
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("first ClientMessage must contain start_request")
            return

        start_req = first.start_request
        run_id = start_req.run_id or "unnamed"
        run_dir = os.path.join(self._runs_dir, run_id)

        run_status: dict[str, Any] = {
            "status": "running",
            "metric_calls_used": 0,
            "message": "",
        }
        with self._runs_lock:
            self._runs[run_id] = run_status

        outbound: "queue.Queue[pb.ServerMessage | None]" = queue.Queue()
        adapter = RemoteAdapter(outbound)

        def reader() -> None:
            try:
                for msg in request_iterator:
                    if msg.HasField("evaluate_batch_response"):
                        adapter.deliver_evaluate_response(msg.evaluate_batch_response)
                    elif msg.HasField("reflective_dataset_response"):
                        adapter.deliver_reflective_response(msg.reflective_dataset_response)
                    elif msg.HasField("start_request"):
                        logger.warning("ignoring extra start_request after run started")
            except Exception as e:
                logger.info("client stream closed: %s", e)
                adapter.cancel(e)
            else:
                adapter.cancel()

        def runner() -> None:
            try:
                trainset = [RemoteExample.from_proto(e) for e in start_req.trainset]
                valset_proto = list(start_req.valset)
                valset = [RemoteExample.from_proto(e) for e in valset_proto] if valset_proto else None

                max_metric_calls = start_req.max_metric_calls or None
                callback = _ProgressCallback(outbound, start_req.max_metric_calls, run_status)

                os.makedirs(run_dir, exist_ok=True)
                result = gepa.optimize(
                    seed_candidate=dict(start_req.seed_candidate),
                    trainset=trainset,
                    valset=valset,
                    adapter=adapter,
                    reflection_lm=HARDCODED_REFLECTION_LM,
                    max_metric_calls=max_metric_calls,
                    run_dir=run_dir,
                    callbacks=[callback],
                    raise_on_exception=True,
                )

                best_idx = result.best_idx
                best_candidate = result.candidates[best_idx]
                best_score = result.val_aggregate_scores[best_idx]
                outbound.put(
                    pb.ServerMessage(
                        optimization_complete=pb.OptimizationComplete(
                            run_id=run_id,
                            best_candidate=dict(best_candidate),
                            best_score=float(best_score),
                        )
                    )
                )
                run_status["status"] = "complete"
            except Exception as e:
                logger.exception("optimization run %s failed", run_id)
                run_status["status"] = "failed"
                run_status["message"] = str(e)
                outbound.put(
                    pb.ServerMessage(
                        optimization_error=pb.OptimizationError(run_id=run_id, message=str(e))
                    )
                )
            finally:
                outbound.put(None)
                adapter.cancel()

        threading.Thread(target=reader, name=f"gepa-rpc-reader-{run_id}", daemon=True).start()
        threading.Thread(target=runner, name=f"gepa-rpc-runner-{run_id}", daemon=True).start()

        while True:
            msg = outbound.get()
            if msg is None:
                return
            yield msg
