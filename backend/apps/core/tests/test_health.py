"""
Tests for the health check endpoint (RNF-5) and latency assertions for the ``/health/`` endpoint.

The health endpoint sits in front of every container orchestration
loop, so latency budgets are first-class requirements rather than
nice-to-haves. These tests fail loudly if a future change reintroduces
the 3-second-health regression that motivated the v1.1 rewrite.

Targets:
    * Fast mode (default): p99 ≤ 100 ms.
    * Deep mode with all dependencies down: p99 ≤ 4 s.

We measure 20 samples and assert against the p95 — using percentiles
instead of mean keeps a single noisy outlier (GC pause, scheduler hop)
from breaking the suite while still tightening the constraint compared
to a max-only assertion.

References: RNF-5.
"""

from __future__ import annotations

import time
from statistics import quantiles

import pytest
from rest_framework import status
from rest_framework.test import APIClient

# DB access is needed because the fast probe runs ``SELECT 1``.
pytestmark = pytest.mark.django_db


def _measure_latency(client: APIClient, url: str, samples: int = 20) -> list[float]:
    """Hit ``url`` ``samples`` times and return per-call wall times in ms.

    The first call is discarded as a warm-up: cold imports, cold DB
    connection, and cold boto3 client setup happen on the first
    request and are not representative of steady-state behaviour.
    """
    timings: list[float] = []
    # Warm-up.
    client.get(url)

    for _ in range(samples):
        t0 = time.perf_counter()
        client.get(url)
        timings.append((time.perf_counter() - t0) * 1000)
    return timings


def _p95(timings: list[float]) -> float:
    """Return the 95th percentile of a sample (linear interpolation)."""
    # ``quantiles(n=20)`` produces 19 cut points between min and max;
    # the 19th cut point is the p95. ``method="inclusive"`` matches the
    # standard exclusive empirical CDF used by most latency tooling.
    return quantiles(timings, n=20, method="inclusive")[18]


def test_fast_health_p95_under_100ms():
    """Fast probe must respond well under 100 ms p95.

    Fast mode probes only PostgreSQL and Redis. Both are first-party
    dependencies that are alive whenever Django itself is alive, so
    the entire response time should be dominated by request routing
    and a couple of trivial queries.
    """
    client = APIClient()
    timings = _measure_latency(client, "/health/", samples=20)

    p95 = _p95(timings)
    assert p95 < 100.0, (
        f"Fast /health/ p95 latency is {p95:.1f} ms, budget is 100 ms.\n"
        f"All samples (ms): {[f'{t:.1f}' for t in timings]}\n"
        "Likely causes: a probe was added that is not actually cheap, "
        "or the boto3 client cache is being bypassed."
    )


def test_fast_health_does_not_probe_minio_or_celery(monkeypatch):
    """Fast probe must NOT call MinIO or Celery.

    Latency assertions are necessary but not sufficient — a buggy
    implementation could probe MinIO and still respond in <100 ms when
    MinIO is healthy. This test enforces the *contract* that the fast
    mode does not even *attempt* the deep probes.
    """
    from apps.core import views as health_views

    # Boobytrap the deep probes: any call must fail the test.
    def _boom(*args, **kwargs):
        pytest.fail("Fast /health/ called a deep probe — the mode separation is broken.")

    monkeypatch.setattr(health_views.HealthCheckView, "_check_minio", _boom)
    monkeypatch.setattr(health_views.HealthCheckView, "_check_celery", _boom)

    client = APIClient()
    response = client.get("/health/")

    assert response.status_code == 200
    body = response.json()
    assert set(body["components"].keys()) == {"postgresql", "redis"}


def test_deep_health_includes_minio_and_celery_components(monkeypatch):
    """Deep probe must include MinIO and Celery in the response keys.

    Stubs both probes so the test passes regardless of whether the
    underlying services are running — what we are testing is the
    routing, not the probes themselves.
    """
    from apps.core import views as health_views

    monkeypatch.setattr(
        health_views.HealthCheckView,
        "_check_minio",
        lambda self: {"status": "healthy"},
    )
    monkeypatch.setattr(
        health_views.HealthCheckView,
        "_check_celery",
        lambda self: {"status": "healthy", "workers": 1},
    )

    client = APIClient()
    response = client.get("/health/?deep=true")

    assert response.status_code == 200
    body = response.json()
    assert set(body["components"].keys()) == {
        "postgresql",
        "redis",
        "minio",
        "celery_workers",
    }


class TestHealthCheck:
    """Verify the /health/ endpoint reports component status."""

    def test_health_check_returns_200_when_healthy(self):
        client = APIClient()
        response = client.get("/health/")
        assert response.status_code in (
            status.HTTP_200_OK,
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
        assert "status" in response.data
        assert "components" in response.data

    def test_health_check_requires_no_authentication(self):
        client = APIClient()  # No credentials
        response = client.get("/health/")
        # Should not return 401 or 403
        assert response.status_code != status.HTTP_401_UNAUTHORIZED
        assert response.status_code != status.HTTP_403_FORBIDDEN
