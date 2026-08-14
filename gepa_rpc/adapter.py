"""RemoteAdapter is1 a GEPAAdapter whose evaluate/make_reflective_dataset calls
are proxied across a gRPC stream to a connected client.

The adapter is owned by the server-side RunOptimization handler. It places
outbound ServerMessages onto a queue (drained by the handler's response
generator) and blocks on Future objects keyed by request_id. The handler's
reader thread fulfills those futures when the matching ClientMessage arrives.
"""

from __future__ import annotations

import json
import queue
import threading
import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from concurrent.futures import Future
from typing import Any

from gepa.core.adapter import EvaluationBatch, GEPAAdapter

from gepa_rpc.conversions import RemoteExample, RemoteTrajectory, reflective_data_to_python
from gepa_rpc.generated import gepa_pb2 as pb


class RemoteAdapterCancelled(RuntimeError):
    """Raised inside the optimizer thread when the client stream is gone."""


class RemoteAdapter(GEPAAdapter[RemoteExample, RemoteTrajectory, str]):
    def __init__(self, outbound: "queue.Queue[pb.ServerMessage | None]"):
        self._outbound = outbound
        self._pending: dict[str, Future] = {}
        self._lock = threading.Lock()
        self._cancelled = False
        self._cancel_exc: BaseException | None = None

    # Wiring
    def deliver_evaluate_response(self, resp: pb.EvaluateBatchResponse) -> None:
        self._resolve(resp.request_id, resp)

    def deliver_reflective_response(self, resp: pb.ReflectiveDatasetResponse) -> None:
        self._resolve(resp.request_id, resp)

    def cancel(self, exc: BaseException | None = None) -> None:
        """Called when the client stream ends. Fail every in-flight call."""
        err = exc or RemoteAdapterCancelled("client stream closed")
        with self._lock:
            self._cancelled = True
            self._cancel_exc = err
            pending = list(self._pending.values())
            self._pending.clear()
        for fut in pending:
            if not fut.done():
                fut.set_exception(err)

    def _resolve(self, request_id: str, payload: Any) -> None:
        with self._lock:
            fut = self._pending.pop(request_id, None)
        if fut is not None and not fut.done():
            fut.set_result(payload)

    def _new_pending(self) -> tuple[str, Future]:
        request_id = str(uuid.uuid4())
        fut: Future = Future()
        with self._lock:
            if self._cancelled:
                # Don't queue work after cancel.
                raise self._cancel_exc or RemoteAdapterCancelled("adapter cancelled")
            self._pending[request_id] = fut
        return request_id, fut

    # GEPAAdapter
    def evaluate(
        self,
        batch: list[RemoteExample],
        candidate: dict[str, str],
        capture_traces: bool = False,
    ) -> EvaluationBatch[RemoteTrajectory, str]:
        request_id, fut = self._new_pending()
        request = pb.EvaluateBatchRequest(
            request_id=request_id,
            candidate=dict(candidate),
            batch=[ex.to_proto() for ex in batch],
            capture_traces=capture_traces,
        )
        self._outbound.put(pb.ServerMessage(evaluate_batch_request=request))
        resp: pb.EvaluateBatchResponse = fut.result()

        outputs = list(resp.outputs)
        scores = list(resp.scores)
        if len(outputs) != len(batch) or len(scores) != len(batch):
            raise ValueError(
                f"client returned mismatched evaluate response: "
                f"got {len(outputs)} outputs, {len(scores)} scores for batch of {len(batch)}"
            )

        trajectories: list[RemoteTrajectory] | None = None
        if capture_traces:
            if len(resp.trajectories) != len(batch):
                raise ValueError(
                    f"capture_traces=True but client returned {len(resp.trajectories)} trajectories "
                    f"for batch of {len(batch)}"
                )
            trajectories = [RemoteTrajectory.from_proto(t) for t in resp.trajectories]

        return EvaluationBatch(outputs=outputs, scores=scores, trajectories=trajectories)

    def make_reflective_dataset(
        self,
        candidate: dict[str, str],
        eval_batch: EvaluationBatch[RemoteTrajectory, str],
        components_to_update: list[str],
    ) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        if eval_batch.trajectories is None:
            raise ValueError("trajectories are required to build a reflective dataset")

        request_id, fut = self._new_pending()
        request = pb.ReflectiveDatasetRequest(
            request_id=request_id,
            candidate=dict(candidate),
            components_to_update=list(components_to_update),
            trajectories=[t.to_proto() for t in eval_batch.trajectories],
        )
        self._outbound.put(pb.ServerMessage(reflective_dataset_request=request))
        resp: pb.ReflectiveDatasetResponse = fut.result()
        return reflective_data_to_python(resp)


def _example_to_proto(ex: Any) -> pb.Example:
    """Convert a dataset example to a proto Example."""
    if isinstance(ex, dict):
        if "fields" in ex:
            return pb.Example(id=str(ex.get("id", "")), fields={k: str(v) for k, v in ex["fields"].items()})
        return pb.Example(id="", fields={k: str(v) for k, v in ex.items()})
    if hasattr(ex, "id") and hasattr(ex, "fields"):
        return pb.Example(id=str(ex.id), fields={k: str(v) for k, v in ex.fields.items()})
    return pb.Example(id="", fields={"value": str(ex)})


class OmniRemoteEvaluator:
    """batch_evaluator passed to optimize_anything.

    Groups (candidate, example) pairs by candidate and sends one
    OmniEvaluateBatchRequest per unique candidate, blocking on a Future
    until the client responds. The reader thread resolves futures via
    deliver_response().
    """

    def __init__(self, outbound: "queue.Queue[pb.OmniServerMessage | None]"):
        self._outbound = outbound
        self._pending: dict[str, Future] = {}
        self._lock = threading.Lock()
        self._cancelled = False
        self._cancel_exc: BaseException | None = None

    # Wiring
    def deliver_response(self, resp: pb.OmniEvaluateBatchResponse) -> None:
        self._resolve(resp.request_id, resp)

    def cancel(self, exc: BaseException | None = None) -> None:
        err = exc or RemoteAdapterCancelled("client stream closed")
        with self._lock:
            self._cancelled = True
            self._cancel_exc = err
            pending = list(self._pending.values())
            self._pending.clear()
        for fut in pending:
            if not fut.done():
                fut.set_exception(err)

    def _resolve(self, request_id: str, payload: Any) -> None:
        with self._lock:
            fut = self._pending.pop(request_id, None)
        if fut is not None and not fut.done():
            fut.set_result(payload)

    def _new_pending(self) -> tuple[str, Future]:
        request_id = str(uuid.uuid4())
        fut: Future = Future()
        with self._lock:
            if self._cancelled:
                raise self._cancel_exc or RemoteAdapterCancelled("adapter cancelled")
            self._pending[request_id] = fut
        return request_id, fut

    # batch_evaluator
    def __call__(
        self,
        pairs: list[tuple[Any, Any]],
    ) -> list[tuple[float, dict[str, Any]]]:
        if not pairs:
            return []

        # Group by candidate. GEPA typically sends one candidate per call
        # but the contract allows mixed batches
        groups: dict[str, list[tuple[int, Any]]] = defaultdict(list)
        for idx, (candidate, example) in enumerate(pairs):
            groups[str(candidate)].append((idx, example))

        results: list[tuple[float, dict[str, Any]]] = [(0.0, {})] * len(pairs)

        for candidate_str, indexed_examples in groups.items():
            indices = [i for i, _ in indexed_examples]
            examples = [ex for _, ex in indexed_examples]

            request_id, fut = self._new_pending()
            self._outbound.put(pb.OmniServerMessage(
                evaluate_batch_request=pb.OmniEvaluateBatchRequest(
                    request_id=request_id,
                    candidate=candidate_str,
                    batch=[_example_to_proto(ex) for ex in examples],
                )
            ))
            resp: pb.OmniEvaluateBatchResponse = fut.result()

            if len(resp.scores) != len(examples):
                raise ValueError(
                    f"client returned {len(resp.scores)} scores for batch of {len(examples)}"
                )

            for i, (score, side_info_json) in enumerate(zip(resp.scores, resp.side_infos)):
                try:
                    side_info = json.loads(side_info_json) if side_info_json else {}
                except json.JSONDecodeError:
                    side_info = {"raw": side_info_json}
                results[indices[i]] = (float(score), side_info)

        return results
