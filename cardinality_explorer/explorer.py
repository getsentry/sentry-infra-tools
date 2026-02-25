#!/usr/bin/env python3
"""
Interactive Time Series Cardinality Explorer.

Demonstrates how labels affect the cardinality of time series metrics,
and how correlated vs uncorrelated labels differ in their impact.

Usage:
    python -m cardinality_explorer.explorer          # run built-in scenarios
    python -m cardinality_explorer.explorer --interactive  # interactive mode
"""

from __future__ import annotations

import argparse
import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from .cardinality import (
    CorrelatedGroup,
    Label,
    Metric,
    enumerate_all_series,
    simulate_adding_label,
)

console = Console()


# ── Pretty-printing helpers ─────────────────────────────────────────


def render_metric_summary(metric: Metric) -> Table:
    table = Table(
        title=f"Metric: {metric.name}",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Label", style="bold")
    table.add_column("Unique Values", justify="right")
    table.add_column("Type", style="dim")

    for label in metric.independent_labels:
        table.add_row(
            label.name,
            str(label.cardinality),
            "independent",
        )
    for group in metric.correlated_groups:
        group_names = " + ".join(l.name for l in group.labels)
        for i, label in enumerate(group.labels):
            type_text = (
                f"correlated ({group_names})" if i == 0 else "  ↳ (same group)"
            )
            table.add_row(label.name, str(label.cardinality), type_text)

    return table


def render_cardinality_comparison(
    metric: Metric,
) -> Panel:
    actual = metric.cardinality
    theoretical = metric.theoretical_max
    savings = metric.savings_from_correlation()

    bar_width = 40
    if theoretical > 0:
        filled = max(1, int(bar_width * actual / theoretical))
    else:
        filled = bar_width

    bar = "█" * filled + "░" * (bar_width - filled)
    pct = (actual / theoretical * 100) if theoretical > 0 else 100

    lines = [
        f"  Actual cardinality:      [bold green]{actual:>10,}[/] time series",
        f"  Theoretical max:         [bold red]{theoretical:>10,}[/] time series",
        f"  Saved by correlations:   [bold yellow]{savings:>10,}[/] time series",
        "",
        f"  [green]{bar}[/]  {pct:.1f}% of max",
    ]
    return Panel(
        "\n".join(lines),
        title="Cardinality",
        border_style="blue",
    )


def render_series_table(metric: Metric, max_rows: int = 30) -> Table | None:
    if metric.cardinality > max_rows:
        return None

    all_series = enumerate_all_series(metric)
    if not all_series:
        return None

    label_names = sorted(all_series[0].keys())
    table = Table(
        title=f"All {len(all_series)} Time Series",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("#", justify="right", style="dim")
    for name in label_names:
        table.add_column(name)

    for i, series in enumerate(all_series, 1):
        table.add_row(str(i), *[series[n] for n in label_names])

    return table


def show_metric(metric: Metric) -> None:
    console.print()
    console.print(render_metric_summary(metric))
    console.print(render_cardinality_comparison(metric))
    series_table = render_series_table(metric)
    if series_table:
        console.print(series_table)
    else:
        console.print(
            f"  [dim](Too many series to enumerate — "
            f"{metric.cardinality:,} total)[/dim]"
        )
    console.print()


def show_step(title: str, description: str) -> None:
    console.print()
    console.rule(f"[bold]{title}[/bold]")
    console.print(f"  [italic]{description}[/italic]")


# ── Built-in scenarios ──────────────────────────────────────────────


def scenario_basic_multiplication() -> None:
    """Show how independent labels multiply cardinality."""
    console.print(
        Panel(
            "[bold]Scenario 1: Independent Labels Multiply Cardinality[/bold]\n\n"
            "When labels are independent (uncorrelated), every possible\n"
            "combination of values can occur. The total number of time\n"
            "series is the PRODUCT of each label's cardinality.",
            border_style="green",
        )
    )

    # Start with just the metric name — 1 series.
    metric = Metric(name="http_requests_total")
    show_step("Step 0", "Bare metric — no labels yet")
    console.print("  Cardinality: [bold]1[/bold] (just the metric itself)")

    # Add method.
    show_step("Step 1", 'Add label "method" with 3 values: GET, POST, DELETE')
    metric.independent_labels.append(
        Label("method", ["GET", "POST", "DELETE"])
    )
    show_metric(metric)

    # Add status_code.
    show_step(
        "Step 2",
        'Add label "status" with 5 values: 200, 201, 400, 404, 500',
    )
    metric.independent_labels.append(
        Label("status", ["200", "201", "400", "404", "500"])
    )
    show_metric(metric)

    # Add handler.
    show_step(
        "Step 3",
        'Add label "handler" with 4 values: /api/users, /api/orders, '
        "/api/products, /healthz",
    )
    metric.independent_labels.append(
        Label(
            "handler",
            ["/api/users", "/api/orders", "/api/products", "/healthz"],
        )
    )
    show_metric(metric)

    console.print(
        "[bold yellow]→ Notice:[/bold yellow] 3 × 5 × 4 = "
        f"[bold]{3 * 5 * 4}[/bold] — pure multiplication!\n"
    )


def scenario_correlated_labels() -> None:
    """Show how correlated labels DON'T multiply cardinality."""
    console.print(
        Panel(
            "[bold]Scenario 2: Correlated Labels — Cardinality Savings[/bold]\n\n"
            "When label values are correlated (e.g. a city determines its\n"
            "country), only the actually-observed combinations matter.\n"
            "The cardinality equals the number of distinct tuples, which\n"
            "is much less than the full Cartesian product.",
            border_style="green",
        )
    )

    # Independent: region with 3 values.
    show_step(
        "Step 1",
        'Start with independent label "environment" (prod, staging, dev)',
    )
    metric = Metric(
        name="deployment_status",
        independent_labels=[
            Label("environment", ["prod", "staging", "dev"]),
        ],
    )
    show_metric(metric)

    # Add correlated country + city.
    show_step(
        "Step 2",
        'Add CORRELATED labels "country" and "city".\n'
        "  Each city belongs to exactly one country:\n"
        "    US → NYC, LA, Chicago\n"
        "    UK → London, Manchester\n"
        "    DE → Berlin",
    )
    country_label = Label(
        "country", ["US", "US", "US", "UK", "UK", "DE"]
    )
    city_label = Label(
        "city",
        ["NYC", "LA", "Chicago", "London", "Manchester", "Berlin"],
    )
    correlated = CorrelatedGroup(
        labels=[country_label, city_label],
        observed_tuples=[
            ("US", "NYC"),
            ("US", "LA"),
            ("US", "Chicago"),
            ("UK", "London"),
            ("UK", "Manchester"),
            ("DE", "Berlin"),
        ],
    )
    metric.correlated_groups.append(correlated)
    show_metric(metric)

    console.print(
        "[bold yellow]→ Key insight:[/bold yellow]\n"
        "  If country & city were independent: "
        f"3 × 6 = [bold red]{3 * 6}[/bold red] combos\n"
        f"  Actual correlated tuples:          [bold green]{6}[/bold green] combos\n"
        f"  Total time series: 3 envs × 6 tuples = "
        f"[bold green]{3 * 6}[/bold green] (not 3 × 3 × 6 = "
        f"[bold red]{3 * 3 * 6}[/bold red])\n"
    )


def scenario_what_if() -> None:
    """Show the impact of adding high-cardinality labels."""
    console.print(
        Panel(
            "[bold]Scenario 3: What-If — The Danger of High-Cardinality Labels[/bold]\n\n"
            "See what happens when you add a label with many unique values,\n"
            "like user_id or request_id. This is the #1 cause of cardinality\n"
            "explosions in production monitoring systems.",
            border_style="green",
        )
    )

    metric = Metric(
        name="api_latency_seconds",
        independent_labels=[
            Label("method", ["GET", "POST"]),
            Label("endpoint", ["/users", "/orders", "/products", "/auth"]),
            Label("status", ["2xx", "4xx", "5xx"]),
        ],
    )

    show_step("Baseline", "A well-designed metric with 3 labels")
    show_metric(metric)

    # Add pod_id with 50 values.
    show_step(
        "What if we add pod_id?",
        "pod_id has 50 unique values (one per pod in the cluster)",
    )
    metric_with_pod = simulate_adding_label(
        metric,
        Label("pod_id", [f"pod-{i}" for i in range(50)]),
    )
    show_metric(metric_with_pod)

    # Add user_id with 10000 values.
    show_step(
        "What if we add user_id?",
        "user_id has 10,000 unique values — a classic cardinality bomb!",
    )
    metric_with_user = simulate_adding_label(
        metric,
        Label("user_id", [f"user-{i}" for i in range(10_000)]),
    )
    show_metric(metric_with_user)

    console.print(
        "[bold red]→ WARNING:[/bold red] Adding user_id turned 24 series "
        f"into [bold]{metric_with_user.cardinality:,}[/bold]!\n"
        "  This would overwhelm most TSDB backends.\n"
    )


def scenario_correlation_spectrum() -> None:
    """Show the full range from fully correlated to fully independent."""
    console.print(
        Panel(
            "[bold]Scenario 4: The Correlation Spectrum[/bold]\n\n"
            "Labels can range from fully correlated (1:1 mapping)\n"
            "to partially correlated to fully independent. This\n"
            "scenario shows all three cases side-by-side.",
            border_style="green",
        )
    )

    tree = Tree("[bold]Correlation Spectrum[/bold]")

    # Fully correlated: each A value maps to exactly one B value.
    full_node = tree.add("[bold green]Fully Correlated (1:1 mapping)[/bold green]")
    full_group = CorrelatedGroup(
        labels=[
            Label("service", ["web", "api", "worker"]),
            Label("team", ["frontend", "backend", "infra"]),
        ],
        observed_tuples=[
            ("web", "frontend"),
            ("api", "backend"),
            ("worker", "infra"),
        ],
    )
    full_node.add(
        f"service(3) × team(3): theoretical = {full_group.theoretical_max}, "
        f"actual = {full_group.cardinality}"
    )
    full_node.add(f"Correlation ratio: {full_group.correlation_ratio:.2f}")

    # Partially correlated: some overlap.
    partial_node = tree.add(
        "[bold yellow]Partially Correlated[/bold yellow]"
    )
    partial_group = CorrelatedGroup(
        labels=[
            Label("service", ["web", "api", "api", "worker", "worker"]),
            Label("team", ["frontend", "backend", "frontend", "infra", "backend"]),
        ],
        observed_tuples=[
            ("web", "frontend"),
            ("api", "backend"),
            ("api", "frontend"),
            ("worker", "infra"),
            ("worker", "backend"),
        ],
    )
    partial_node.add(
        f"service(3) × team(3): theoretical = {partial_group.theoretical_max}, "
        f"actual = {partial_group.cardinality}"
    )
    partial_node.add(f"Correlation ratio: {partial_group.correlation_ratio:.2f}")

    # Fully independent: every combination observed.
    independent_node = tree.add(
        "[bold red]Fully Independent (no correlation)[/bold red]"
    )
    services = ["web", "api", "worker"]
    teams = ["frontend", "backend", "infra"]
    indep_group = CorrelatedGroup(
        labels=[
            Label("service", services * 3),
            Label("team", teams * 3),
        ],
        observed_tuples=[
            (s, t) for s in services for t in teams
        ],
    )
    independent_node.add(
        f"service(3) × team(3): theoretical = {indep_group.theoretical_max}, "
        f"actual = {indep_group.cardinality}"
    )
    independent_node.add(
        f"Correlation ratio: {indep_group.correlation_ratio:.2f}"
    )

    console.print(tree)
    console.print()
    console.print(
        "[bold]Correlation ratio[/bold]: "
        "0.0 = fully correlated → 1.0 = fully independent\n"
        "  Measured as log(actual) / log(theoretical) so partial\n"
        "  correlation is visible on the scale.\n"
    )


# ── Interactive mode ────────────────────────────────────────────────


def interactive_mode() -> None:
    """Let the user build a metric interactively and see cardinality change."""
    console.print(
        Panel(
            "[bold]Interactive Cardinality Explorer[/bold]\n\n"
            "Build a metric step by step. Add labels and see how\n"
            "each one affects the total cardinality.",
            border_style="cyan",
        )
    )

    name = console.input("[bold]Metric name:[/bold] ").strip() or "my_metric"
    metric = Metric(name=name)
    console.print(f"\nCreated metric [bold]{name}[/bold] — cardinality: 1\n")

    while True:
        console.print("[bold]Commands:[/bold]")
        console.print("  [cyan]add[/cyan]       — Add an independent label")
        console.print("  [cyan]correlate[/cyan] — Add a label correlated with an existing one")
        console.print("  [cyan]show[/cyan]      — Show current metric details")
        console.print("  [cyan]list[/cyan]      — List all time series (if cardinality ≤ 30)")
        console.print("  [cyan]quit[/cyan]      — Exit")
        console.print()

        cmd = console.input("[bold]>[/bold] ").strip().lower()

        if cmd in ("quit", "exit", "q"):
            break

        elif cmd == "show":
            show_metric(metric)

        elif cmd == "list":
            series_table = render_series_table(metric)
            if series_table:
                console.print(series_table)
            else:
                console.print(
                    f"  [dim]Cardinality is {metric.cardinality:,} — "
                    "too large to enumerate.[/dim]"
                )

        elif cmd == "add":
            label_name = console.input("  Label name: ").strip()
            if not label_name:
                continue
            values_str = console.input(
                "  Values (comma-separated): "
            ).strip()
            values = [v.strip() for v in values_str.split(",") if v.strip()]
            if not values:
                console.print("  [red]No values provided.[/red]")
                continue

            old_card = metric.cardinality
            metric.independent_labels.append(Label(label_name, values))
            new_card = metric.cardinality
            console.print(
                f"\n  Added [bold]{label_name}[/bold] with "
                f"{len(set(values))} unique values."
            )
            console.print(
                f"  Cardinality: {old_card:,} → [bold]{new_card:,}[/bold] "
                f"(×{new_card // old_card if old_card else 'N/A'})\n"
            )

        elif cmd == "correlate":
            if not metric.all_labels:
                console.print("  [red]Add at least one label first.[/red]")
                continue

            console.print("  Existing labels:")
            for label in metric.all_labels:
                console.print(f"    - {label.name} ({label.cardinality} values)")

            corr_with = console.input(
                "  Correlate with which existing label? "
            ).strip()
            new_name = console.input("  New label name: ").strip()
            if not new_name:
                continue

            console.print(
                f"  Enter observed (existing_value, new_value) pairs, "
                f"one per line."
            )
            console.print("  Empty line to finish.")

            tuples: list[tuple[str, ...]] = []
            existing_vals: list[str] = []
            new_vals: list[str] = []
            while True:
                line = console.input("    ").strip()
                if not line:
                    break
                parts = [p.strip() for p in line.split(",")]
                if len(parts) != 2:
                    console.print("    [red]Need exactly 2 comma-separated values.[/red]")
                    continue
                existing_vals.append(parts[0])
                new_vals.append(parts[1])
                tuples.append(tuple(parts))

            if not tuples:
                console.print("  [red]No tuples provided.[/red]")
                continue

            try:
                old_card = metric.cardinality
                metric = simulate_adding_label(
                    metric,
                    Label(new_name, new_vals),
                    correlated_with=corr_with,
                    observed_tuples=tuples,
                )
                new_card = metric.cardinality
                console.print(
                    f"\n  Added [bold]{new_name}[/bold] correlated with "
                    f"[bold]{corr_with}[/bold]."
                )
                console.print(
                    f"  Cardinality: {old_card:,} → [bold]{new_card:,}[/bold]\n"
                )
            except ValueError as e:
                console.print(f"  [red]Error: {e}[/red]")

        else:
            console.print(f"  [red]Unknown command: {cmd}[/red]")


# ── Main ────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Time Series Cardinality Explorer"
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Run in interactive mode (build your own metric)",
    )
    parser.add_argument(
        "--scenario",
        type=int,
        choices=[1, 2, 3, 4],
        help="Run a specific scenario (1-4)",
    )
    args = parser.parse_args()

    if args.interactive:
        interactive_mode()
        return

    console.print()
    console.print(
        Panel(
            "[bold]Time Series Cardinality Explorer[/bold]\n\n"
            "This tool demonstrates how labels affect the cardinality\n"
            "of time series metrics. Cardinality = the total number of\n"
            "unique time series, determined by all possible combinations\n"
            "of label values.\n\n"
            "[bold green]Key rule:[/bold green]\n"
            "  • Uncorrelated labels [bold]MULTIPLY[/bold] cardinality\n"
            "  • Correlated labels [bold]DON'T[/bold] multiply — only\n"
            "    observed combinations count",
            border_style="bright_blue",
        )
    )

    scenarios = {
        1: scenario_basic_multiplication,
        2: scenario_correlated_labels,
        3: scenario_what_if,
        4: scenario_correlation_spectrum,
    }

    if args.scenario:
        scenarios[args.scenario]()
    else:
        for fn in scenarios.values():
            fn()


if __name__ == "__main__":
    main()
