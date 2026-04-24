# gepa-polyglot

Working under the guidance of Lakshya A. Agrawal.

GEPA-Polyglot is a bidirectional gRPC interface in front of [GEPA](https://github.com/gepa-ai/gepa) so Rust and JavaScript developers can drive `gepa.optimize()` while providing **native-language evaluators**.

The core idea: GEPA wants to call `evaluate(batch, candidate)` from Python, but your evaluator lives in TypeScript or Rust. Instead of forcing you to port it, gepa-polyglot opens a long-lived gRPC stream and **inverts the call direction** — the Python server sends `EvaluateBatchRequest` messages over the stream whenever GEPA needs a score, and the client runs the native callback and replies on the same stream.

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
```

## Roadmap

| Week | Deliverable | Status |
|---|---|---|
| 1 | `proto/gepa.proto` + Python stubs | ✅ shipped |
| 2 | Python wrapper (`RemoteAdapter`) that proxies `GEPAAdapter.evaluate` / `make_reflective_dataset` over the stream | ✅ shipped |
| 3 | Progress streaming + `run_dir` checkpointing for disconnect-resume; `gepa-rpc` CLI to launch the server | ✅ shipped |
| 4 | `@gepa/sdk` TypeScript client with a single `client.optimize()` async API | ✅ shipped |
| 5 | Rust crate via `tonic` | ⏳ next |
| 6 | Dockerize the server + GitHub Actions to auto-publish SDKs whenever the Python core updates | ⏳ |

## Quickstart

### 1. Start the server

```bash
uv sync                                # or pip install -e .
gepa-rpc --port 50051 --runs-dir ./runs
```

The server hosts `GEPAService.RunOptimization`. State per run is checkpointed under `./runs/<run_id>/` so reconnecting with the same `run_id` resumes from the last saved iteration.

### 2. Drive it from TypeScript

```bash
cd sdk/typescript
npm install
npm run build
npx tsx examples/basic.ts
```

`examples/basic.ts` walks through the full SDK API with a stand-in evaluator.

## Repo layout

```
proto/gepa.proto             canonical service + message definitions
gepa_rpc/                    Python server
  generated/                 protoc output (committed)
  conversions.py             RemoteExample / RemoteTrajectory dataclasses
  adapter.py                 RemoteAdapter (implements GEPAAdapter)
  servicer.py                GEPAServicer + bidi RunOptimization handler
  server.py                  build_server() / serve()
  cli.py                     `gepa-rpc` console script
sdk/typescript/              @gepa/sdk npm package
  src/{types,client,index}.ts
  examples/basic.ts
  proto/gepa.proto           synced from repo root via scripts/sync-proto.sh
scripts/compile_proto.sh     regenerates gepa_rpc/generated/ from proto/gepa.proto
```

## Notes

- Server hardcodes `reflection_lm = "gpt-5"`. `StartRequest.reflection_lm` is wired through the proto but currently ignored.
- Real disconnect/resume relies on `gepa.optimize`'s built-in `run_dir` checkpointing.
- TS SDK uses `@grpc/proto-loader` at runtime; user-facing types are hand-written in `sdk/typescript/src/types.ts`.
