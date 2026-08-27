"""Integration tests for GEPAServicer.

These tests spin up a real gRPC server and drive it with a Python stub client.
The gepa optimizer and reflection LM are monkey-patched so no real LLM calls
are made — the tests verify the gRPC wire protocol and adapter pipeline only.
"""

from __future__ import annotations

import queue
import socket
from concurrent import futures
from types import SimpleNamespace
from unittest.mock import patch

import grpc
import pytest

from gepa_rpc.generated import gepa_pb2 as pb
from gepa_rpc.generated import gepa_pb2_grpc as pb_grpc
from gepa_rpc.servicer import GEPAServicer


# ------------------------------------------------------------------ fixtures


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture()
def stub(tmp_path):
    """Start a GEPAServicer on a random port; yield a connected stub."""
    port = _free_port()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    pb_grpc.add_GEPAServiceServicer_to_server(
        GEPAServicer(runs_dir=str(tmp_path / "runs")), server
    )
    server.add_insecure_port(f"[::]:{port}")
    server.start()

    channel = grpc.insecure_channel(f"localhost:{port}")
    yield pb_grpc.GEPAServiceStub(channel)

    channel.close()
    server.stop(grace=0)


# ------------------------------------------------------------------ helpers


_TRAINSET = [
    pb.Example(id="1", fields={"input": "2+2", "answer": "4"}),
    pb.Example(id="2", fields={"input": "3+3", "answer": "6"}),
]

_SEED = {"instructions": "Answer the question."}


def _run_optimize(stub, *, run_id: str, fake_optimize, seed=None, trainset=None, max_metric_calls=20):
    """Drive a full RunOptimization round-trip with a mock optimizer.

    The fake_optimize callable receives the same kwargs the servicer passes to
    gepa.optimize. It must return a GEPAResult-shaped namespace.

    The test client automatically responds to EvaluateBatchRequest messages
    with score=1.0 for every example.
    """
    seed = seed or _SEED
    trainset = trainset or _TRAINSET

    req_q: queue.Queue = queue.Queue()
    req_q.put(pb.ClientMessage(
        start_request=pb.StartRequest(
            run_id=run_id,
            seed_candidate=seed,
            trainset=trainset,
            max_metric_calls=max_metric_calls,
            reflection_lm="fake",
        )
    ))

    def gen():
        while True:
            msg = req_q.get()
            if msg is None:
                return
            yield msg

    final = None
    with patch("gepa_rpc.servicer.gepa.optimize", side_effect=fake_optimize):
        call = stub.RunOptimization(gen())
        for msg in call:
            if msg.HasField("evaluate_batch_request"):
                req = msg.evaluate_batch_request
                req_q.put(pb.ClientMessage(
                    evaluate_batch_response=pb.EvaluateBatchResponse(
                        request_id=req.request_id,
                        outputs=["ok"] * len(req.batch),
                        scores=[1.0] * len(req.batch),
                    )
                ))
            elif msg.HasField("optimization_complete") or msg.HasField("optimization_error"):
                final = msg
                req_q.put(None)
                break

    return final


def _run_optimize_omni(stub, *, run_id: str, fake_optimize_anything, dataset=None):
    """Drive a full RunOptimizationOmni round-trip with a mock optimizer."""
    dataset = dataset or [
        pb.Example(id="1", fields={"x": "hello"}),
        pb.Example(id="2", fields={"x": "world"}),
    ]

    req_q: queue.Queue = queue.Queue()
    req_q.put(pb.OmniClientMessage(
        start_request=pb.OmniStartRequest(
            run_id=run_id,
            seed_candidate="Classify the input.",
            dataset=dataset,
            max_evals=10,
            reflection_lm="fake",
        )
    ))

    def gen():
        while True:
            msg = req_q.get()
            if msg is None:
                return
            yield msg

    final = None
    with patch("gepa_rpc.servicer.optimize_anything", side_effect=fake_optimize_anything):
        call = stub.RunOptimizationOmni(gen())
        for msg in call:
            if msg.HasField("evaluate_batch_request"):
                req = msg.evaluate_batch_request
                req_q.put(pb.OmniClientMessage(
                    evaluate_batch_response=pb.OmniEvaluateBatchResponse(
                        request_id=req.request_id,
                        scores=[0.8] * len(req.batch),
                        side_infos=["{}"] * len(req.batch),
                    )
                ))
            elif msg.HasField("optimization_complete") or msg.HasField("optimization_error"):
                final = msg
                req_q.put(None)
                break

    return final


# ------------------------------------------------------------------ tests: RunOptimization


def test_optimize_returns_complete(stub):
    """Happy path: mock optimizer returns a result → OptimizationComplete."""
    def fake_optimize(*, seed_candidate, **_):
        return SimpleNamespace(
            candidates=[dict(seed_candidate)],
            best_idx=0,
            val_aggregate_scores=[0.75],
        )

    msg = _run_optimize(stub, run_id="opt-happy", fake_optimize=fake_optimize)

    assert msg is not None
    assert msg.HasField("optimization_complete")
    c = msg.optimization_complete
    assert c.run_id == "opt-happy"
    assert c.best_score == pytest.approx(0.75)
    assert dict(c.best_candidate) == _SEED


def test_optimize_evaluate_proxied(stub):
    """adapter.evaluate is called → EvaluateBatchRequest flows to client → scores return."""
    received_batches: list = []

    def fake_optimize(*, seed_candidate, trainset, adapter, **_):
        batch = list(trainset)
        result = adapter.evaluate(batch, dict(seed_candidate), capture_traces=False)
        received_batches.append((batch, result.scores))
        return SimpleNamespace(
            candidates=[dict(seed_candidate)],
            best_idx=0,
            val_aggregate_scores=[sum(result.scores) / len(result.scores)],
        )

    _run_optimize(stub, run_id="opt-proxy", fake_optimize=fake_optimize)

    assert len(received_batches) == 1
    batch, scores = received_batches[0]
    assert len(batch) == len(_TRAINSET)
    assert scores == [1.0] * len(_TRAINSET)


def test_optimize_error_propagated(stub):
    """An exception in the optimizer thread becomes a generic OptimizationError (no leak)."""
    def fake_optimize(**_):
        raise RuntimeError("boom")

    msg = _run_optimize(stub, run_id="opt-err", fake_optimize=fake_optimize)

    assert msg is not None
    assert msg.HasField("optimization_error")
    assert "boom" not in msg.optimization_error.message
    assert msg.optimization_error.message == "optimization failed"


def test_get_status_complete(stub):
    """GetStatus returns COMPLETE after a successful RunOptimization."""
    def fake_optimize(*, seed_candidate, **_):
        return SimpleNamespace(
            candidates=[dict(seed_candidate)],
            best_idx=0,
            val_aggregate_scores=[0.5],
        )

    run_id = "status-test"
    _run_optimize(stub, run_id=run_id, fake_optimize=fake_optimize)

    resp = stub.GetStatus(pb.StatusRequest(run_id=run_id))
    assert resp.status == pb.StatusResponse.COMPLETE


def test_get_status_unknown(stub):
    """GetStatus for an unknown run_id returns UNKNOWN."""
    resp = stub.GetStatus(pb.StatusRequest(run_id="no-such-run"))
    assert resp.status == pb.StatusResponse.UNKNOWN


# ------------------------------------------------------------------ tests: RunOptimizationOmni


def test_optimize_omni_returns_complete(stub):
    """Happy path: Omni mock returns a result → OmniOptimizationComplete."""
    from gepa.oa.engine import Result

    def fake_optimize_anything(*, seed_candidate, **_):
        return Result(best_candidate=seed_candidate or "", best_score=0.9, total_evals=4)

    msg = _run_optimize_omni(stub, run_id="omni-happy", fake_optimize_anything=fake_optimize_anything)

    assert msg is not None
    assert msg.HasField("optimization_complete")
    c = msg.optimization_complete
    assert c.run_id == "omni-happy"
    assert c.best_score == pytest.approx(0.9)
    assert c.total_evals == 4


def test_optimize_omni_evaluator_proxied(stub):
    """batch_evaluator is called → OmniEvaluateBatchRequest flows to client → scores return."""
    from gepa.oa.engine import Result

    received: list = []

    def fake_optimize_anything(*, seed_candidate, batch_evaluator, dataset, **_):
        pairs = [(seed_candidate, ex) for ex in (dataset or [])[:2]]
        results = batch_evaluator(pairs)
        received.append(results)
        best_score = max(r[0] for r in results) if results else 0.0
        return Result(best_candidate=seed_candidate or "", best_score=best_score, total_evals=len(pairs))

    _run_optimize_omni(stub, run_id="omni-proxy", fake_optimize_anything=fake_optimize_anything)

    assert len(received) == 1
    scores = [r[0] for r in received[0]]
    # proto float is 32-bit; compare with tolerance
    assert scores == pytest.approx([0.8, 0.8], rel=1e-5)


def test_optimize_omni_error_propagated(stub):
    """An exception in the Omni optimizer thread becomes a generic OptimizationError (no leak)."""
    def fake_optimize_anything(**_):
        raise ValueError("omni boom")

    msg = _run_optimize_omni(stub, run_id="omni-err", fake_optimize_anything=fake_optimize_anything)

    assert msg is not None
    assert msg.HasField("optimization_error")
    assert "omni boom" not in msg.optimization_error.message
    assert msg.optimization_error.message == "optimization failed"


# ------------------------------------------------------------------ edge case tests


def test_invalid_run_id_rejected(stub):
    """run_id with path traversal characters is rejected with INVALID_ARGUMENT."""
    import grpc as grpc_module

    def fake_optimize(**_):
        return SimpleNamespace(candidates=[{}], best_idx=0, val_aggregate_scores=[0.0])

    req_q: queue.Queue = queue.Queue()
    req_q.put(pb.ClientMessage(
        start_request=pb.StartRequest(
            run_id="../../../etc/passwd",
            seed_candidate=_SEED,
            trainset=_TRAINSET,
            max_metric_calls=5,
        )
    ))

    def gen():
        while True:
            msg = req_q.get()
            if msg is None:
                return
            yield msg

    with patch("gepa_rpc.servicer.gepa.optimize", side_effect=fake_optimize):
        call = stub.RunOptimization(gen())
        try:
            list(call)
            req_q.put(None)
            pytest.fail("expected RpcError")
        except grpc_module.RpcError as e:
            req_q.put(None)
            assert e.code() == grpc_module.StatusCode.INVALID_ARGUMENT


def test_optimize_empty_trainset(stub):
    """RunOptimization with an empty trainset completes without error."""
    def fake_optimize(**_):
        return SimpleNamespace(candidates=[{"instructions": "x"}], best_idx=0, val_aggregate_scores=[0.0])

    msg = _run_optimize(stub, run_id="opt-empty", fake_optimize=fake_optimize, trainset=[])
    assert msg is not None
    assert msg.HasField("optimization_complete")


def test_optimize_omni_no_seed(stub):
    """RunOptimizationOmni with no seed_candidate (empty string) completes."""
    from gepa.oa.engine import Result

    def fake_optimize_anything(*, seed_candidate, **_):
        return Result(best_candidate="generated", best_score=0.5, total_evals=2)

    req_q: queue.Queue = queue.Queue()
    req_q.put(pb.OmniClientMessage(
        start_request=pb.OmniStartRequest(
            run_id="omni-noseed",
            seed_candidate="",
            max_evals=5,
            reflection_lm="fake",
        )
    ))

    def gen():
        while True:
            msg = req_q.get()
            if msg is None:
                return
            yield msg

    final = None
    with patch("gepa_rpc.servicer.optimize_anything", side_effect=fake_optimize_anything):
        call = stub.RunOptimizationOmni(gen())
        for msg in call:
            if msg.HasField("optimization_complete") or msg.HasField("optimization_error"):
                final = msg
                req_q.put(None)
                break

    assert final is not None
    assert final.HasField("optimization_complete")
    assert final.optimization_complete.best_candidate == "generated"


def test_optimize_omni_invalid_run_id_rejected(stub):
    """Omni run_id with path traversal is rejected."""
    import grpc as grpc_module

    req_q: queue.Queue = queue.Queue()
    req_q.put(pb.OmniClientMessage(
        start_request=pb.OmniStartRequest(
            run_id="../../bad",
            seed_candidate="test",
            max_evals=5,
        )
    ))

    def gen():
        while True:
            msg = req_q.get()
            if msg is None:
                return
            yield msg

    def fake(**_):
        from gepa.oa.engine import Result
        return Result(best_candidate="x", best_score=1.0, total_evals=1)

    with patch("gepa_rpc.servicer.optimize_anything", side_effect=fake):
        call = stub.RunOptimizationOmni(gen())
        try:
            list(call)
            req_q.put(None)
            pytest.fail("expected RpcError")
        except grpc_module.RpcError as e:
            req_q.put(None)
            assert e.code() == grpc_module.StatusCode.INVALID_ARGUMENT
