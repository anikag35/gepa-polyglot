# gepa-polyglot

Working under the guidance of Lakshya A. Agrawal.

GEPA-Polyglot is a bidirectional gRPC interface in front of [GEPA](https://github.com/gepa-ai/gepa) so Rust and JavaScript developers can drive prompt optimization while providing **native-language evaluators**.

Two endpoints are available:

- **`RunOptimization`** wraps `gepa.optimize`. The client supplies structured `(trainset, valset, seed_candidate)` and implements `evaluate` + `makeReflectiveDataset`. Full multi-component prompt optimization.
- **`RunOptimizationOmni`** wraps `optimize_anything`. The client supplies a single `evaluate(candidate, example) -> (score, side_info)` callback. Simpler contract; supports all engines (`gepa`, `autoresearch`, `best_of_n`, `meta_harness`).

```
            ┌──────────────────────────┐                 ┌──────────────────────────┐
            │  client (TS / Rust)      │                 │  gepa-rpc server (py)    │
            │                          │  StartRequest   │                          │
  user ───▶ │  client.optimize({       │ ──────────────▶ │  GEPAServicer            │
            │    evaluate,             │                 │     │                    │
            │    makeReflectiveDataset │ ◀── Eval req ── │     ▼                    │
            │  })                      │ ── Eval resp ─▶ │  gepa.optimize(          │
            │                          │                 │     adapter=             │
            │                          │ ◀── Progress ── │     RemoteAdapter)       │
            │                          │ ◀─ Complete ─── │                          │
            └──────────────────────────┘                 └──────────────────────────┘

            ┌──────────────────────────┐                 ┌──────────────────────────┐
            │  client (TS / Rust)      │                 │  gepa-rpc server (py)    │
            │                          │  StartRequest   │                          │
  user ───▶ │  client.optimizeOmni({   │ ──────────────▶ │  GEPAServicer            │
            │    evaluate,             │                 │     │                    │
            │  })                      │ ◀── Eval req ── │     ▼                    │
            │                          │ ── Eval resp ─▶ │  optimize_anything(      │
            │                          │                 │     batch_evaluator=     │
            │                          │ ◀── Progress ── │     OmniRemoteEvaluator) │
            │                          │ ◀─ Complete ─── │                          │
            └──────────────────────────┘                 └──────────────────────────┘
```

## Quickstart

### 1. Start the server

```bash
uv sync                                # or pip install -e .
gepa-rpc --port 50051 --runs-dir ./runs
```

State per run is checkpointed under `./runs/<run_id>/` so reconnecting with the same `run_id` resumes from the last saved iteration.

### 2. Drive it from TypeScript

```bash
cd sdk/typescript
npm install && npm run build
npx tsx examples/basic.ts
```

`examples/basic.ts` walks through the full `client.optimize()` API with a stand-in evaluator.

### 3. Run integration tests

```bash
python -m pytest tests/test_integration.py -v
```

Tests spin up a real gRPC server and drive both endpoints end-to-end with a mock optimizer (no LLM calls needed).

## Repo layout

```
proto/gepa.proto             canonical service + message definitions
gepa_rpc/                    Python server
  generated/                 protoc output (committed)
  conversions.py             RemoteExample / RemoteTrajectory dataclasses
  adapter.py                 RemoteAdapter (GEPAAdapter) + OmniRemoteEvaluator
  servicer.py                GEPAServicer -- RunOptimization + RunOptimizationOmni handlers
  server.py                  build_server() / serve()
  cli.py                     `gepa-rpc` console script
sdk/typescript/              @gepa/sdk npm package
  src/{types,client,index}.ts
  examples/basic.ts
  proto/gepa.proto           synced from repo root via scripts/sync-proto.sh
sdk/rust/                    gepa-sdk Rust crate
  src/{client,types,error}.rs
  examples/basic.rs
scripts/compile_proto.sh     regenerates gepa_rpc/generated/ from proto/gepa.proto
tests/test_integration.py    gRPC integration tests (pytest)
```

## Notes

- `reflection_lm` defaults to `"openai/gpt-5.1"`. Override it per-run via `StartRequest.reflection_lm` or `OmniStartRequest.reflection_lm`.
- Disconnect-resume relies on `gepa.optimize`'s built-in `run_dir` checkpointing. Re-issue `RunOptimization` with the same `run_id` to resume.
- The TypeScript SDK uses `@grpc/proto-loader` at runtime; user-facing types are hand-written in `sdk/typescript/src/types.ts`.
- `OmniRemoteEvaluator` groups `(candidate, example)` pairs by candidate before sending each batch request, matching the `batch_evaluator` contract from `optimize_anything`.
