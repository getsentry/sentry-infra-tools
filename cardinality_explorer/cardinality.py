"""
Core cardinality computation engine for time series metrics.

Key insight: In time series databases like Prometheus, each unique combination
of label values creates a distinct time series. With N labels that are fully
independent (uncorrelated), the total cardinality is the product of all label
cardinalities: |L1| × |L2| × ... × |LN|.

However, when labels are correlated (e.g., "city" determines "country"), the
actual number of observed combinations is much smaller than the theoretical
maximum. Correlated labels form "groups" whose combined cardinality equals
the number of distinct tuples, NOT the product of individual cardinalities.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field


@dataclass
class Label:
    """A single label (dimension) on a time series metric."""

    name: str
    values: list[str]

    @property
    def cardinality(self) -> int:
        return len(set(self.values))

    def __repr__(self) -> str:
        return f"Label({self.name!r}, {self.cardinality} values)"


@dataclass
class CorrelatedGroup:
    """A group of labels whose values are correlated.

    Instead of multiplying their individual cardinalities, the cardinality
    of the group equals the number of distinct *tuples* of values that
    actually co-occur.

    Example:
        country=["US","US","UK"], city=["NYC","LA","London"]
        => 3 distinct tuples, NOT 2×3=6
    """

    labels: list[Label]
    # Each entry is one observed tuple of values (one per label, same order).
    observed_tuples: list[tuple[str, ...]]

    @property
    def cardinality(self) -> int:
        return len(set(self.observed_tuples))

    @property
    def theoretical_max(self) -> int:
        """Cardinality if the labels were fully independent."""
        result = 1
        for label in self.labels:
            result *= label.cardinality
        return result

    @property
    def correlation_ratio(self) -> float:
        """0 = fully correlated (1 tuple), 1 = fully independent.

        Measured on a log scale so that partial correlation is visible.
        """
        actual = self.cardinality
        theoretical = self.theoretical_max
        if theoretical <= 1:
            return 1.0
        if actual <= 1:
            return 0.0
        return math.log(actual) / math.log(theoretical)


@dataclass
class Metric:
    """A time series metric with labels, some of which may be correlated."""

    name: str
    # Independent labels (each multiplies cardinality).
    independent_labels: list[Label] = field(default_factory=list)
    # Groups of correlated labels.
    correlated_groups: list[CorrelatedGroup] = field(default_factory=list)

    @property
    def cardinality(self) -> int:
        """Total number of unique time series."""
        result = 1
        for label in self.independent_labels:
            result *= label.cardinality
        for group in self.correlated_groups:
            result *= group.cardinality
        return result

    @property
    def theoretical_max(self) -> int:
        """Cardinality if ALL labels were fully independent."""
        result = 1
        for label in self.independent_labels:
            result *= label.cardinality
        for group in self.correlated_groups:
            result *= group.theoretical_max
        return result

    @property
    def all_labels(self) -> list[Label]:
        labels = list(self.independent_labels)
        for group in self.correlated_groups:
            labels.extend(group.labels)
        return labels

    def savings_from_correlation(self) -> int:
        """How many time series are avoided thanks to correlations."""
        return self.theoretical_max - self.cardinality


def enumerate_all_series(metric: Metric) -> list[dict[str, str]]:
    """Enumerate every distinct time series as a list of label dicts.

    Useful for small cardinalities to visually inspect the combinations.
    """
    # Build the independent axes.
    independent_axes: list[list[dict[str, str]]] = []
    for label in metric.independent_labels:
        independent_axes.append(
            [{label.name: v} for v in sorted(set(label.values))]
        )

    # Build correlated group axes.
    for group in metric.correlated_groups:
        unique_tuples = sorted(set(group.observed_tuples))
        axis: list[dict[str, str]] = []
        for tup in unique_tuples:
            axis.append(
                {
                    label.name: val
                    for label, val in zip(group.labels, tup)
                }
            )
        independent_axes.append(axis)

    # Cartesian product across all axes.
    results: list[dict[str, str]] = []
    for combo in itertools.product(*independent_axes):
        merged: dict[str, str] = {}
        for d in combo:
            merged.update(d)
        results.append(merged)
    return results


def simulate_adding_label(
    metric: Metric,
    new_label: Label,
    correlated_with: str | None = None,
    observed_tuples: list[tuple[str, ...]] | None = None,
) -> Metric:
    """Return a new Metric with an additional label.

    If correlated_with is specified, the new label is added to the
    correlated group containing that label.
    """
    import copy

    new_metric = copy.deepcopy(metric)

    if correlated_with is None:
        new_metric.independent_labels.append(new_label)
        return new_metric

    # Find the group or independent label to merge with.
    # First check existing groups.
    for group in new_metric.correlated_groups:
        for existing_label in group.labels:
            if existing_label.name == correlated_with:
                group.labels.append(new_label)
                if observed_tuples is not None:
                    group.observed_tuples = list(observed_tuples)
                return new_metric

    # Check independent labels.
    for i, existing_label in enumerate(new_metric.independent_labels):
        if existing_label.name == correlated_with:
            # Promote to a correlated group.
            new_metric.independent_labels.pop(i)
            if observed_tuples is None:
                raise ValueError(
                    "observed_tuples is required when correlating "
                    "a previously-independent label"
                )
            new_group = CorrelatedGroup(
                labels=[existing_label, new_label],
                observed_tuples=list(observed_tuples),
            )
            new_metric.correlated_groups.append(new_group)
            return new_metric

    raise ValueError(f"Label {correlated_with!r} not found in metric")
