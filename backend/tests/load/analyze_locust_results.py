"""
Render performance graphs from a Locust run.

Usage:
  python -m tests.load.analyze_locust_results \
      --input-prefix=/tmp/loadtest_results \
      --output-dir=/tmp/loadtest_graphs \
      --lang=es
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd

_RNF1_P95_TARGETS_MS: dict[str, float] = {
    "auth/login": 500.0,
    "student/profile": 200.0,
    "student/notifications": 200.0,
    "student/instance/detail": 200.0,
    "student/instance/download": 2000.0,
    "student/review-request": 500.0,
    "grader/my-tasks": 200.0,
    "grader/instance/detail": 200.0,
    "grader/grade/manual": 500.0,
    "grader/annotations/create": 500.0,
    "manager/publish": 5000.0,
    "ingest/drop_file": 100.0,
}

_DEFAULT_TARGET_MS = 1000.0

_WINDOW_SIZE = 5
_SKIP_SECONDS = 5

_LABELS = {
    "en": {
        "fig1_title": "Latency percentiles per endpoint",
        "fig1_xlabel": "Latency (ms)",
        "fig1_ylabel": "Endpoint",
        "fig2_title": "Throughput over time",
        "fig2_xlabel": "Wall time (s)",
        "fig2_ylabel": "Requests / second",
        "fig3_title": "Error rate over time",
        "fig3_xlabel": "Wall time (s)",
        "fig3_ylabel": "Failures / second",
        "fig4_title": "p95 vs RNF-1 target",
        "fig4_xlabel": "p95 latency (ms)",
        "fig4_ylabel": "Endpoint",
        "fig4_target_label": "RNF-1 target",
        "fig5_title": "Latency vs concurrent users",
        "fig5_xlabel": "Wall time (s)",
        "fig5_ylabel": "Latency (ms)",
        "fig6_title": "Errors vs concurrent users",
        "fig6_xlabel": "Wall time (s)",
        "fig6_ylabel": "Failures / second",
        "fig7_title": "Throughput vs concurrent users",
        "fig7_xlabel": "Wall time (s)",
        "fig7_ylabel": "Requests / second",
        "users_label": "Concurrent users",
        "no_history": "No history data",
        "no_usable_history": "No usable history data",
    },
    "es": {
        "fig1_title": "Percentiles de latencia por endpoint",
        "fig1_xlabel": "Latencia (ms)",
        "fig1_ylabel": "Endpoint",
        "fig2_title": "Throughput a lo largo del tiempo",
        "fig2_xlabel": "Tiempo transcurrido (s)",
        "fig2_ylabel": "Peticiones / segundo",
        "fig3_title": "Tasa de errores a lo largo del tiempo",
        "fig3_xlabel": "Tiempo transcurrido (s)",
        "fig3_ylabel": "Fallos / segundo",
        "fig4_title": "p95 frente a objetivo RNF-1",
        "fig4_xlabel": "Latencia p95 (ms)",
        "fig4_ylabel": "Endpoint",
        "fig4_target_label": "Objetivo RNF-1",
        "fig5_title": "Latencia frente a usuarios concurrentes",
        "fig5_xlabel": "Tiempo transcurrido (s)",
        "fig5_ylabel": "Latencia (ms)",
        "fig6_title": "Errores frente a usuarios concurrentes",
        "fig6_xlabel": "Tiempo transcurrido (s)",
        "fig6_ylabel": "Fallos / segundo",
        "fig7_title": "Throughput frente a usuarios concurrentes",
        "fig7_xlabel": "Tiempo transcurrido (s)",
        "fig7_ylabel": "Peticiones / segundo",
        "users_label": "Usuarios concurrentes",
        "no_history": "No hay datos históricos",
        "no_usable_history": "No hay datos históricos utilizables",
    },
}


def _load_stats(prefix: str) -> pd.DataFrame:
    path = Path(prefix + "_stats.csv")

    if not path.exists():
        raise FileNotFoundError(f"Missing {path}")

    df = pd.read_csv(path)

    return df[df["Name"] != "Aggregated"].reset_index(drop=True)


def _load_history(prefix: str) -> pd.DataFrame:
    path = Path(prefix + "_stats_history.csv")

    if not path.exists():
        return pd.DataFrame()

    return pd.read_csv(path)


def _prepare_windowed_history(history: pd.DataFrame) -> pd.DataFrame:
    if history.empty:
        return pd.DataFrame()

    df = history[history["Name"] == "Aggregated"].copy()

    if df.empty:
        df = history.copy()

    df = df.reset_index(drop=True)

    if len(df) < 2:
        return pd.DataFrame()

    df["t"] = (df["Timestamp"] - df["Timestamp"].min()).astype(float)

    req_col = (
        "Total Request Count" if "Total Request Count" in df.columns else "Total Requests Count"
    )

    avg_col = (
        "Average Response Time"
        if "Average Response Time" in df.columns
        else "Total Average Response Time"
    )

    rows = []

    for i in range(1, len(df)):
        count_now = df.iloc[i][req_col]
        count_prev = df.iloc[i - 1][req_col]

        avg_now = df.iloc[i][avg_col]
        avg_prev = df.iloc[i - 1][avg_col]

        delta_count = count_now - count_prev

        if delta_count > 0:
            delta_sum = (count_now * avg_now) - (count_prev * avg_prev)
            window_avg = delta_sum / delta_count
        else:
            window_avg = None

        rows.append(
            {
                "t": df.iloc[i]["t"],
                "users": df.iloc[i]["User Count"],
                "latency_ms": window_avg,
                "rps": df.iloc[i].get(
                    "Requests/s",
                    df.iloc[i].get("Total Requests/s", 0),
                ),
                "fps": df.iloc[i].get(
                    "Failures/s",
                    df.iloc[i].get("Total Failures/s", 0),
                ),
            }
        )

    result = pd.DataFrame(rows)

    if result.empty:
        return result

    result = result[result["t"] >= _SKIP_SECONDS]

    for col in ["latency_ms", "rps", "fps"]:
        result[f"{col}_smooth"] = result[col].rolling(window=_WINDOW_SIZE, min_periods=1).mean()

    return result


def _fig1(stats: pd.DataFrame, L: dict) -> plt.Figure:
    p50 = next(
        (c for c in stats.columns if c in {"50%", "50%ile", "Median Response Time"}),
        None,
    )

    p95 = next(
        (c for c in stats.columns if c in {"95%", "95%ile"}),
        None,
    )

    p99 = next(
        (c for c in stats.columns if c in {"99%", "99%ile"}),
        None,
    )

    if not all([p50, p95, p99]):
        raise RuntimeError("Missing percentile columns in stats CSV.")

    df = stats.sort_values(p95, ascending=True)

    fig, ax = plt.subplots(figsize=(9, max(3.5, 0.4 * len(df) + 2)))

    y = range(len(df))
    w = 0.27

    ax.barh([i - w for i in y], df[p50], height=w, label="p50")
    ax.barh(y, df[p95], height=w, label="p95")
    ax.barh([i + w for i in y], df[p99], height=w, label="p99")

    ax.set_yticks(y)
    ax.set_yticklabels(df["Name"])

    ax.set_xlabel(L["fig1_xlabel"])
    ax.set_ylabel(L["fig1_ylabel"])
    ax.set_title(L["fig1_title"])

    ax.legend(loc="lower right")

    ax.grid(axis="x", linestyle=":", alpha=0.5)

    fig.tight_layout()

    return fig


def _fig2(history: pd.DataFrame, L: dict) -> plt.Figure:
    fig, ax = plt.subplots(figsize=(9, 4))

    if history.empty:
        ax.text(0.5, 0.5, L["no_history"], ha="center", va="center")
        ax.set_title(L["fig2_title"])
        return fig

    df = history[history["Name"] == "Aggregated"].copy()

    if df.empty:
        df = history.copy()

    df["t"] = (df["Timestamp"] - df["Timestamp"].min()).astype(float)

    rps = "Requests/s" if "Requests/s" in df.columns else "Total Requests/s"

    ax.plot(df["t"], df[rps], linewidth=1.6)

    ax.fill_between(df["t"], df[rps], alpha=0.2)

    ax.set_xlabel(L["fig2_xlabel"])
    ax.set_ylabel(L["fig2_ylabel"])
    ax.set_title(L["fig2_title"])

    ax.grid(linestyle=":", alpha=0.5)

    fig.tight_layout()

    return fig


def _fig3(history: pd.DataFrame, L: dict) -> plt.Figure:
    fig, ax = plt.subplots(figsize=(9, 4))

    if history.empty:
        ax.text(0.5, 0.5, L["no_history"], ha="center", va="center")
        ax.set_title(L["fig3_title"])
        return fig

    df = history[history["Name"] == "Aggregated"].copy()

    if df.empty:
        df = history.copy()

    df["t"] = (df["Timestamp"] - df["Timestamp"].min()).astype(float)

    fps = "Failures/s" if "Failures/s" in df.columns else "Total Failures/s"

    ax.plot(df["t"], df[fps], color="tab:red", linewidth=1.6)

    ax.fill_between(df["t"], df[fps], color="tab:red", alpha=0.2)

    ax.set_xlabel(L["fig3_xlabel"])
    ax.set_ylabel(L["fig3_ylabel"])
    ax.set_title(L["fig3_title"])

    ax.grid(linestyle=":", alpha=0.5)

    fig.tight_layout()

    return fig


def _fig4(stats: pd.DataFrame, L: dict) -> plt.Figure:
    p95 = next(
        (c for c in stats.columns if c in {"95%", "95%ile"}),
        None,
    )

    if not p95:
        raise RuntimeError("p95 column not found")

    df = stats[["Name", p95]].copy()

    df["target"] = df["Name"].map(_RNF1_P95_TARGETS_MS).fillna(_DEFAULT_TARGET_MS)

    df["color"] = [
        "tab:red" if v > t else "tab:green" for v, t in zip(df[p95], df["target"], strict=False)
    ]

    df = df.sort_values(p95, ascending=True)

    fig, ax = plt.subplots(figsize=(9, max(3.5, 0.4 * len(df) + 2)))

    y = range(len(df))

    ax.barh(y, df[p95], color=df["color"], alpha=0.8)

    ax.scatter(
        df["target"],
        y,
        marker="D",
        color="black",
        s=40,
        label=L["fig4_target_label"],
    )

    ax.set_yticks(y)
    ax.set_yticklabels(df["Name"])

    ax.set_xlabel(L["fig4_xlabel"])
    ax.set_ylabel(L["fig4_ylabel"])
    ax.set_title(L["fig4_title"])

    ax.legend(loc="lower right")

    ax.grid(axis="x", linestyle=":", alpha=0.5)

    fig.tight_layout()

    return fig


def _plot_metric_vs_users(
    history: pd.DataFrame,
    metric_col: str,
    metric_label: str,
    title: str,
    users_label: str,
) -> plt.Figure:
    fig, ax1 = plt.subplots(figsize=(10, 4.5))

    if history.empty:
        ax1.text(0.5, 0.5, "No history data", ha="center", va="center")
        ax1.set_title(title)
        return fig

    df = _prepare_windowed_history(history)

    if df.empty:
        ax1.text(0.5, 0.5, "No usable history data", ha="center", va="center")
        ax1.set_title(title)
        return fig

    metric_smooth = f"{metric_col}_smooth"

    ax2 = ax1.twinx()

    ax1.plot(
        df["t"],
        df[metric_smooth],
        linewidth=1.8,
    )

    ax1.fill_between(
        df["t"],
        df[metric_smooth],
        alpha=0.2,
    )

    ax2.plot(
        df["t"],
        df["users"],
        linestyle="--",
        color="gray",
        alpha=0.7,
        linewidth=1.2,
    )

    ax2.fill_between(
        df["t"],
        0,
        df["users"],
        color="gray",
        alpha=0.08,
    )

    ax1.set_xlabel("Time (s)")
    ax1.set_ylabel(metric_label)

    ax2.set_ylabel(users_label, color="gray")
    ax2.tick_params(axis="y", labelcolor="gray")

    ax1.set_title(title)

    ax1.grid(linestyle=":", alpha=0.5)

    fig.tight_layout()

    return fig


def _fig5(history: pd.DataFrame, L: dict) -> plt.Figure:
    return _plot_metric_vs_users(
        history=history,
        metric_col="latency_ms",
        metric_label=L["fig5_ylabel"],
        title=L["fig5_title"],
        users_label=L["users_label"],
    )


def _fig6(history: pd.DataFrame, L: dict) -> plt.Figure:
    return _plot_metric_vs_users(
        history=history,
        metric_col="fps",
        metric_label=L["fig6_ylabel"],
        title=L["fig6_title"],
        users_label=L["users_label"],
    )


def _fig7(history: pd.DataFrame, L: dict) -> plt.Figure:
    return _plot_metric_vs_users(
        history=history,
        metric_col="rps",
        metric_label=L["fig7_ylabel"],
        title=L["fig7_title"],
        users_label=L["users_label"],
    )


def main():
    parser = argparse.ArgumentParser(description="Render Locust performance graphs.")

    parser.add_argument(
        "--input-prefix",
        required=True,
        help="Locust --csv prefix",
    )

    parser.add_argument(
        "--output-dir",
        required=True,
    )

    parser.add_argument(
        "--lang",
        choices=["en", "es"],
        default="en",
    )

    args = parser.parse_args()

    out = Path(args.output_dir)

    out.mkdir(parents=True, exist_ok=True)

    L = _LABELS[args.lang]

    stats = _load_stats(args.input_prefix)
    history = _load_history(args.input_prefix)

    figs = [
        ("01_latency_percentiles.png", _fig1(stats, L)),
        ("02_throughput_over_time.png", _fig2(history, L)),
        ("03_error_rate.png", _fig3(history, L)),
        ("04_p95_vs_target.png", _fig4(stats, L)),
        ("05_latency_vs_users.png", _fig5(history, L)),
        ("06_errors_vs_users.png", _fig6(history, L)),
        ("07_throughput_vs_users.png", _fig7(history, L)),
    ]

    for name, fig in figs:
        fig.savefig(out / name, dpi=144)
        plt.close(fig)

        print(f"Wrote {out / name}")

    print(f"\nAll figures in {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
