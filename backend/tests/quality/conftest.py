"""Local pytest configuration for the reliability suite.

Registers the ``reliability`` marker so the test runner does not warn
when collecting it. The marker is local to this directory: production
tests do not see it.

Add the same marker to ``backend/pyproject.toml`` if you want to be
able to deselect it from the top-level ``pytest`` invocation
(``pytest -m "not reliability"``).
"""


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        (
            "reliability: marks reliability suites that consume larger "
            "fixtures (OCR datasets, etc.). Slow by nature; not run by "
            "default in tight CI loops."
        ),
    )
