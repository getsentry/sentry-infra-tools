"""
Serial, verified GKE cluster upgrades.

Lessons from the CVE-2026-98432 rollout (ops#24956) that shape this module:

- A node pool reports the target version as soon as its upgrade operation
  starts, and keeps reporting it if the operation fails. "Done" means every
  node's kubelet is on the target, nothing less.
- gcloud's synchronous upgrade gives up after an hour while the operation
  keeps running server-side. Operations are started with --async and polled
  through the API instead, so a long pool does not need a live gcloud session.
- Re-running an upgrade to the same version only touches the nodes still
  behind, so a failed attempt (zone stockout, autoscaler racing the operation)
  is retried as-is.
- Autoprovisioned pools come and go during a long rollout; a pool that
  disappears is noted and skipped, not an error.
- Fixed-capacity upgrades (max surge 0, max unavailable 1) reimage nodes in
  place and need no spare VM, which is the way through a stockout. The pool's
  original settings are put back once its operation has finished; the API
  refuses the change while one is running.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Optional

UPGRADE_NODES = "UPGRADE_NODES"
UPGRADE_MASTER = "UPGRADE_MASTER"
INCOMPATIBLE_OPERATION = "incompatible operation"


@dataclass(frozen=True)
class Node:
    name: str
    pool: str
    kubelet_version: str
    unschedulable: bool = False


@dataclass(frozen=True)
class PoolNodes:
    new: int = 0
    old: int = 0
    cordoned: int = 0

    @property
    def total(self) -> int:
        return self.new + self.old


def pool_nodes(nodes: Iterable[Node], pool: str, version: str) -> PoolNodes:
    target = version if version.startswith("v") else f"v{version}"
    new = old = cordoned = 0
    for node in nodes:
        if node.pool != pool:
            continue
        if node.kubelet_version == target:
            new += 1
        else:
            old += 1
        if node.unschedulable:
            cordoned += 1
    return PoolNodes(new=new, old=old, cordoned=cordoned)


def op_target(op: Mapping[str, Any]) -> str:
    return op.get("targetLink", "").rsplit("/", 1)[-1]


def op_metrics(op: Mapping[str, Any]) -> dict[str, int]:
    return {
        m["name"]: int(m.get("intValue", 0))
        for m in op.get("progress", {}).get("metrics", [])
    }


def op_failed(op: Mapping[str, Any]) -> bool:
    return bool(op.get("error") or op.get("statusMessage"))


def op_failure(op: Mapping[str, Any]) -> str:
    error = op.get("error")
    if isinstance(error, Mapping) and error.get("message"):
        return str(error["message"])
    return str(op.get("statusMessage", "")) or "unknown error"


def running_pool_ops(
    ops: Iterable[Mapping[str, Any]], cluster: str
) -> dict[str, Mapping[str, Any]]:
    """RUNNING node pool upgrade operations of `cluster`, keyed by pool name."""
    return {
        op_target(op): op
        for op in ops
        if op.get("status") == "RUNNING"
        and op.get("operationType") == UPGRADE_NODES
        and f"/clusters/{cluster}/" in op.get("targetLink", "")
    }


def surge_settings(pool: Mapping[str, Any]) -> dict[str, Any]:
    settings = pool.get("upgradeSettings", {})
    return {
        "strategy": settings.get("strategy", "SURGE"),
        "maxSurge": int(settings.get("maxSurge", 0)),
        "maxUnavailable": int(settings.get("maxUnavailable", 0)),
    }


@dataclass
class PoolResult:
    name: str
    outcome: str  # done, gone, failed, skipped
    attempts: int = 0
    detail: str = ""


@dataclass
class Clients:
    """
    Everything the runner needs from the outside world, so tests can drive it
    without GCP. `gcloud` runs an argv and returns (returncode, stderr).
    """

    get_cluster: Callable[[], Mapping[str, Any]]
    list_ops: Callable[[], Sequence[Mapping[str, Any]]]
    list_nodes: Callable[[], Sequence[Node]]
    gcloud: Callable[[Sequence[str]], tuple[int, str]]
    log: Callable[[str], None] = print
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], float] = time.monotonic


@dataclass
class UpgradeRunner:
    clients: Clients
    project: str
    location: str
    cluster: str
    version: str
    interval: float = 60
    max_attempts: int = 3
    fixed_capacity: bool = False
    dry_run: bool = False
    results: list[PoolResult] = field(default_factory=list)

    # gcloud --------------------------------------------------------------

    def _upgrade_argv(self, *extra: str) -> list[str]:
        return [
            "gcloud",
            "container",
            "clusters",
            "upgrade",
            self.cluster,
            "--project",
            self.project,
            "--location",
            self.location,
            "--cluster-version",
            self.version,
            "--quiet",
            "--async",
            *extra,
        ]

    def _settings_argv(self, pool: str, surge: int, unavailable: int) -> list[str]:
        return [
            "gcloud",
            "container",
            "node-pools",
            "update",
            pool,
            "--cluster",
            self.cluster,
            "--project",
            self.project,
            "--location",
            self.location,
            "--max-surge-upgrade",
            str(surge),
            "--max-unavailable-upgrade",
            str(unavailable),
            "--quiet",
        ]

    def _run(self, argv: Sequence[str]) -> tuple[int, str]:
        self.clients.log("run: " + " ".join(argv))
        if self.dry_run:
            return 0, ""
        return self.clients.gcloud(argv)

    # control plane -------------------------------------------------------

    def upgrade_control_plane(self) -> None:
        cluster = self.clients.get_cluster()
        current = cluster["currentMasterVersion"]
        if current == self.version:
            self.clients.log(f"control plane already at {self.version}")
            return
        self.clients.log(f"control plane {current} -> {self.version}")
        code, err = self._run(self._upgrade_argv("--master"))
        if code != 0:
            raise RuntimeError(f"control plane upgrade failed to start: {err.strip()}")
        if self.dry_run:
            return
        started = self.clients.now()
        while True:
            running = [
                op
                for op in self.clients.list_ops()
                if op.get("status") == "RUNNING"
                and op.get("operationType") == UPGRADE_MASTER
                and op_target(op) == self.cluster
            ]
            got = self.clients.get_cluster()["currentMasterVersion"]
            if not running and got == self.version:
                self.clients.log(f"control plane done in {self._elapsed(started)}")
                return
            if not running and got != self.version:
                raise RuntimeError(
                    f"control plane is {got} after upgrade, expected {self.version}"
                )
            self.clients.log(f"control plane upgrading, {self._elapsed(started)}")
            self.clients.sleep(self.interval)

    # node pools ----------------------------------------------------------

    def upgrade_pools(self, pools: Sequence[str]) -> list[PoolResult]:
        """
        Upgrade `pools` in the given order (all pools behind the target when
        empty). A pool with another upgrade already running is moved to the
        back of the queue and picked up later.
        """
        queue = list(pools) if pools else self._pools_behind()
        deferred: set[str] = set()
        while queue:
            name = queue.pop(0)
            current = {
                p["name"]: p for p in self.clients.get_cluster().get("nodePools", [])
            }
            if name not in current:
                self._record(
                    name, "gone", detail="pool no longer exists (autoscaler removed it)"
                )
                continue
            busy = running_pool_ops(self.clients.list_ops(), self.cluster)
            if name in busy and (queue and name not in deferred):
                self.clients.log(
                    f"pool {name}: upgrade already running, coming back to it"
                )
                deferred.add(name)
                queue.append(name)
                continue
            self._upgrade_pool(current[name], adopt=busy.get(name))
        return self.results

    def _pools_behind(self) -> list[str]:
        nodes = self.clients.list_nodes()
        behind = []
        for pool in self.clients.get_cluster().get("nodePools", []):
            counts = pool_nodes(nodes, pool["name"], self.version)
            if pool["version"] != self.version or counts.old:
                behind.append(pool["name"])
        return behind

    def _upgrade_pool(
        self, pool: Mapping[str, Any], adopt: Optional[Mapping[str, Any]] = None
    ) -> None:
        name = pool["name"]
        original = surge_settings(pool)
        flip = (
            self.fixed_capacity
            and original["strategy"] == "SURGE"
            and (original["maxSurge"], original["maxUnavailable"]) != (0, 1)
        )
        counts = pool_nodes(self.clients.list_nodes(), name, self.version)
        if adopt is None and counts.total and not counts.old:
            self._record(name, "done", detail="all nodes already on target")
            return
        self.clients.log(
            f"pool {name}: {pool['version']} -> {self.version}, "
            f"{counts.new} new / {counts.old} old nodes"
        )
        attempts = 0
        flipped = False
        try:
            if adopt is not None:
                self.clients.log(
                    f"pool {name}: waiting on operation {adopt.get('name')}"
                )
                op = self._wait_for_op(name, adopt["name"])
                if op_failed(op):
                    self.clients.log(
                        f"pool {name}: that operation failed: {op_failure(op)}"
                    )
            while attempts < self.max_attempts:
                if self._verified(name):
                    self._record(name, "done", attempts)
                    return
                if flip and not flipped:
                    self._run(self._settings_argv(name, 0, 1))
                    flipped = True
                attempts += 1
                started = self._start_pool_op(name)
                if started is None:
                    if self.dry_run:
                        self._record(name, "skipped", attempts, "dry run")
                        return
                    self.clients.log(
                        f"pool {name}: another operation holds the pool, waiting"
                    )
                    self.clients.sleep(self.interval)
                    attempts -= 1
                    continue
                op = self._wait_for_op(name, started)
                if op_failed(op):
                    self.clients.log(
                        f"pool {name}: attempt {attempts} failed: {op_failure(op)}"
                    )
                    if attempts < self.max_attempts:
                        self.clients.sleep(self.interval)
            if self._verified(name):
                self._record(name, "done", attempts)
            else:
                counts = pool_nodes(self.clients.list_nodes(), name, self.version)
                self._record(
                    name,
                    "failed",
                    attempts,
                    f"{counts.old} nodes still behind after {attempts} attempts",
                )
        finally:
            if flipped:
                self._run(
                    self._settings_argv(
                        name, original["maxSurge"], original["maxUnavailable"]
                    )
                )

    def _start_pool_op(self, name: str) -> Optional[str]:
        """Start the pool's upgrade; the operation name, or None if the pool is busy."""
        code, err = self._run(self._upgrade_argv("--node-pool", name))
        if self.dry_run:
            return None
        if code != 0:
            if INCOMPATIBLE_OPERATION in err:
                return None
            raise RuntimeError(f"pool {name} upgrade failed to start: {err.strip()}")
        for _ in range(6):
            op = running_pool_ops(self.clients.list_ops(), self.cluster).get(name)
            if op is not None:
                return str(op["name"])
            self.clients.sleep(5)
        # The operation can finish before we see it on a tiny pool.
        return ""

    def _wait_for_op(self, pool: str, op_name: str) -> Mapping[str, Any]:
        started = self.clients.now()
        while True:
            ops = self.clients.list_ops()
            op = next((o for o in ops if o.get("name") == op_name), None)
            if op is None and not op_name:
                op = running_pool_ops(ops, self.cluster).get(pool)
            if op is None or op.get("status") != "RUNNING":
                return op or {}
            self.clients.log(self.progress_line(pool, op, started))
            self.clients.sleep(self.interval)

    def progress_line(self, pool: str, op: Mapping[str, Any], started: float) -> str:
        metrics = op_metrics(op)
        counts = pool_nodes(self.clients.list_nodes(), pool, self.version)
        total = metrics.get("NODES_TOTAL", 0)
        done = min(metrics.get("NODES_COMPLETE", 0), total)
        line = (
            f"pool {pool}: {self._elapsed(started)}, op {done}/{total}, "
            f"live {counts.new} new / {counts.old} old, {counts.cordoned} cordoned"
        )
        pdb = metrics.get("NODE_PDB_DELAY_SECONDS", 0)
        if pdb:
            line += f", pdb delay {pdb // 60}m"
        return line

    def _verified(self, pool: str) -> bool:
        counts = pool_nodes(self.clients.list_nodes(), pool, self.version)
        return counts.old == 0

    def _record(
        self, name: str, outcome: str, attempts: int = 0, detail: str = ""
    ) -> None:
        self.results.append(PoolResult(name, outcome, attempts, detail))
        suffix = f" ({detail})" if detail else ""
        self.clients.log(f"pool {name}: {outcome}{suffix}")

    def _elapsed(self, started: float) -> str:
        return f"{int((self.clients.now() - started) // 60)}m"


def watch_report(
    cluster: Mapping[str, Any],
    ops: Sequence[Mapping[str, Any]],
    nodes: Sequence[Node],
    version: str,
    now: float,
    op_started: Callable[[Mapping[str, Any]], float],
) -> str:
    """
    One screen of rollout state: running pool operations with live node
    counts, pools that report the target but still run old nodes, and pools
    not started yet.
    """
    name = cluster["name"]
    pools = cluster.get("nodePools", [])
    running = running_pool_ops(ops, name)
    counts = {p["name"]: pool_nodes(nodes, p["name"], version) for p in pools}
    done = [
        p["name"]
        for p in pools
        if p["version"] == version
        and p["name"] not in running
        and not counts[p["name"]].old
    ]
    stale = [
        p["name"]
        for p in pools
        if p["version"] == version
        and p["name"] not in running
        and counts[p["name"]].old
    ]
    waiting = [
        p["name"]
        for p in pools
        if p["name"] not in running and p["name"] not in done and p["name"] not in stale
    ]
    lines = [
        f"{name}: {len(done)}/{len(pools)} pools on {version}, "
        f"{len(running)} running, {len(waiting) + len(stale)} waiting"
    ]
    if running:
        lines.append("")
        lines.append(
            f"{'RUNNING':<36} {'ELAPSED':>7} {'OP':>9} {'LIVE new/old':>13} {'CORDONED':>8}  PDB DELAY"
        )
        for pool, op in sorted(running.items(), key=lambda kv: op_started(kv[1])):
            m = op_metrics(op)
            c = counts.get(pool, PoolNodes())
            total = m.get("NODES_TOTAL", 0)
            elapsed = int((now - op_started(op)) // 60)
            pdb = m.get("NODE_PDB_DELAY_SECONDS", 0)
            lines.append(
                f"{pool:<36} {elapsed:>6}m {min(m.get('NODES_COMPLETE', 0), total):>4}/{total:<4} "
                f"{c.new:>6}/{c.old:<6} {c.cordoned:>8}  {str(pdb // 60) + 'm' if pdb else ''}"
            )
    if stale:
        lines.append("")
        lines.append(
            "pool reports target but still has old nodes: "
            + ", ".join(f"{p}={counts[p].old}" for p in stale)
        )
    if waiting:
        lines.append("")
        lines.append(
            f"waiting ({len(waiting)}): "
            + ", ".join(f"{p} [{counts[p].total}n]" for p in waiting)
        )
    return "\n".join(lines)
