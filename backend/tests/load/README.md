# Load tests (RNF-2, Phase 12.6)

Locust profile for the SCDEE API. Not run during `pytest`; meant to be
launched against a populated environment.

## Quick start

```bash
pip install locust   # not in base requirements; dev-only
export SCDEE_LOAD_EMAIL=manager@org.local
export SCDEE_LOAD_PASSWORD='MgrPass123!'
export SCDEE_INSTANCE_ID=<uuid>
export SCDEE_PROBLEM_ID=<uuid>

# 50 concurrent users, 5 spawn/sec, 1 minute headless run.
locust -f tests/load/locustfile.py \
    --host=http://localhost:8000 \
    --headless -u 50 -r 5 -t 1m
```

## Scenarios

| User class          | What it does                                 | Tag       | Weight |
|---------------------|----------------------------------------------|-----------|--------|
| `PublishGradesUser` | `GET /profile/` and `GET /notifications/`     | `publish` | 4      |
| `GraderUser`        | `PUT /instances/{id}/problems/{pid}/grade/`   | `grade`   | 1      |
| `IngestionUser`     | `POST /recognition/ingest/` with a 1×1 PNG    | `ingest`  | 1      |

Use `--tags publish` (or `grade` / `ingest`) to isolate a scenario.

## Targets (from RNF-1, RNF-2)

* p95 of CRUD endpoints under 500 ms.
* No 5xx during a 1200-user publication spike.
* Ingestion path keeps recognition latency under 60 s/page even with
  30 sustained graders.
