"""Rule engine that turns a raw health snapshot into findings and solutions."""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Any

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


@dataclass
class Finding:
    id: str
    title: str
    severity: str
    category: str
    detail: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    solutions: list[str] = field(default_factory=list)
    remediation_sql: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Thresholds are deliberately explicit so they can be tuned per environment.
BLOCKED_WAIT_SECONDS = 30
LONG_CALL_SECONDS = 300
TABLESPACE_WARNING_PCT = 85.0
TABLESPACE_CRITICAL_PCT = 95.0
BUFFER_CACHE_WARNING_PCT = 90.0
SOFT_PARSE_WARNING_PCT = 90.0
SLOW_READ_MS = 20.0
SQL_SLOW_SECONDS_PER_EXEC = 1.0
SQL_HIGH_GETS_PER_EXEC = 100_000
RESOURCE_LIMIT_WARNING_PCT = 85.0
STALE_STATS_DAYS = 30


def _pct(part: float, whole: float) -> float:
    return round(100.0 * part / whole, 2) if whole else 0.0


def _check_blocking(snapshot: dict[str, Any]) -> list[Finding]:
    blockers = snapshot.get("blocking_sessions") or []
    if not blockers:
        return []
    worst = max(b.get("waiter_seconds") or 0 for b in blockers)
    severity = "critical" if worst >= BLOCKED_WAIT_SECONDS else "warning"
    return [
        Finding(
            id="blocking_sessions",
            title=f"{len(blockers)} session(s) blocked by lock holders",
            severity=severity,
            category="Concurrency",
            detail=(
                f"The longest waiter has been blocked for {worst}s. Blocked sessions hold "
                "resources and inflate response time for every downstream request."
            ),
            evidence=blockers[:10],
            solutions=[
                "Identify the root blocker (the session that is not itself waiting) and check "
                "whether it is idle in transaction — an application that forgot to commit.",
                "Ask the owning application to commit/rollback; kill the blocker only as a last "
                "resort during an incident.",
                "Longer term: shorten transactions, avoid SELECT ... FOR UPDATE across user "
                "think-time, and add indexes on unindexed foreign keys, which cause table-level "
                "locks on child DML.",
            ],
            remediation_sql=[
                "SELECT sid, serial#, username, status, last_call_et, sql_id FROM v$session "
                f"WHERE sid = {blockers[0].get('blocker_sid')};",
                f"ALTER SYSTEM KILL SESSION '{blockers[0].get('blocker_sid')},"
                f"{blockers[0].get('blocker_serial')}' IMMEDIATE;  -- incident use only",
            ],
        )
    ]


def _check_waits(snapshot: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    advice = {
        "User I/O": (
            "Storage or plan driven: reduce physical reads with better access paths "
            "(indexes, partition pruning) before adding hardware.",
            "Check top SQL by disk reads and look for full scans of large segments.",
        ),
        "Concurrency": (
            "Latch/buffer busy contention: look for hot blocks, high insert concurrency on the "
            "same segment, or excessive hard parsing.",
            "Consider reverse-key/hash-partitioned indexes for monotonic keys.",
        ),
        "Application": (
            "Row lock waits driven by application design; shorten transactions.",
            "Review commit frequency and locking strategy.",
        ),
        "Commit": (
            "log file sync pressure: redo on slow storage or too-frequent commits.",
            "Batch commits and move redo logs to low-latency storage.",
        ),
        "Configuration": (
            "Undersized structures (log buffer, shared pool, redo logs) are throttling work.",
            "Resize the offending structure and re-measure.",
        ),
    }
    current = snapshot.get("current_waits") or []
    for wait in current[:3]:
        sessions_waiting = wait.get("sessions_waiting") or 0
        max_wait = wait.get("max_seconds_in_wait") or 0
        if sessions_waiting < 2 and max_wait < BLOCKED_WAIT_SECONDS:
            continue
        wait_class = wait.get("wait_class") or "Other"
        tips = advice.get(wait_class, ("Investigate this wait class against the top SQL list.",))
        findings.append(
            Finding(
                id=f"wait_{(wait.get('event') or 'unknown').replace(' ', '_')}",
                title=f"Live contention on '{wait.get('event')}' ({wait_class})",
                severity="critical" if max_wait >= BLOCKED_WAIT_SECONDS else "warning",
                category="Waits",
                detail=(
                    f"{sessions_waiting} session(s) are currently waiting, longest {max_wait}s. "
                    "This is the dominant real-time bottleneck."
                ),
                evidence=[wait],
                solutions=list(tips),
                remediation_sql=[
                    "SELECT sid, username, sql_id, event, seconds_in_wait FROM v$session "
                    f"WHERE event = '{wait.get('event')}';"
                ],
            )
        )

    top_events = snapshot.get("top_wait_events") or []
    if top_events:
        total = sum(e.get("time_waited_seconds") or 0 for e in top_events)
        leader = top_events[0]
        share = _pct(leader.get("time_waited_seconds") or 0, total)
        if share >= 50 and (leader.get("avg_wait_ms") or 0) > 5:
            findings.append(
                Finding(
                    id="dominant_wait_event",
                    title=f"'{leader.get('event')}' accounts for {share}% of non-idle wait time",
                    severity="warning",
                    category="Waits",
                    detail=(
                        f"Average wait {leader.get('avg_wait_ms')} ms across "
                        f"{leader.get('total_waits')} waits since startup — a systemic pattern "
                        "rather than a transient spike."
                    ),
                    evidence=top_events[:5],
                    solutions=list(
                        advice.get(
                            leader.get("wait_class") or "",
                            ("Correlate this event with the top SQL and I/O sections below.",),
                        )
                    ),
                )
            )
    return findings


def _check_sql(snapshot: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    for row in (snapshot.get("top_sql") or [])[:5]:
        per_exec = row.get("elapsed_seconds_per_exec") or 0
        gets_per_exec = row.get("gets_per_exec") or 0
        reasons = []
        if per_exec >= SQL_SLOW_SECONDS_PER_EXEC:
            reasons.append(f"{per_exec}s average elapsed per execution")
        if gets_per_exec >= SQL_HIGH_GETS_PER_EXEC:
            reasons.append(f"{int(gets_per_exec):,} buffer gets per execution")
        if not reasons:
            continue
        sql_id = row.get("sql_id")
        findings.append(
            Finding(
                id=f"sql_{sql_id}",
                title=f"Expensive SQL {sql_id} ({', '.join(reasons)})",
                severity="critical" if per_exec >= 5 * SQL_SLOW_SECONDS_PER_EXEC else "warning",
                category="SQL",
                detail=(
                    f"Executed {row.get('executions')} times for "
                    f"{row.get('elapsed_seconds')}s total elapsed and "
                    f"{row.get('disk_reads')} disk reads. Schema: {row.get('parsing_schema_name')}."
                ),
                evidence=[row],
                solutions=[
                    "Inspect the plan for full scans, nested loops over large row sources, or "
                    "implicit datatype conversions that disable index use.",
                    "Refresh optimizer statistics on the referenced tables, then re-check the plan.",
                    "Add or adjust indexes to support the filter/join predicates; consider a "
                    "covering index when only a few columns are projected.",
                    "If the statement is generated with literals, set CURSOR_SHARING or fix the "
                    "application to use bind variables.",
                ],
                remediation_sql=[
                    f"SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY_CURSOR('{sql_id}', NULL, 'ALLSTATS LAST'));",
                    "EXEC DBMS_STATS.GATHER_TABLE_STATS('<owner>', '<table>', "
                    "cascade => TRUE, degree => DBMS_STATS.AUTO_DEGREE);",
                ],
            )
        )
    return findings


def _check_space(snapshot: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    for ts in snapshot.get("tablespaces") or []:
        used = ts.get("used_percent") or 0
        if used < TABLESPACE_WARNING_PCT:
            continue
        critical = used >= TABLESPACE_CRITICAL_PCT
        findings.append(
            Finding(
                id=f"tablespace_{ts.get('tablespace_name')}",
                title=f"Tablespace {ts.get('tablespace_name')} is {used}% full",
                severity="critical" if critical else "warning",
                category="Space",
                detail=(
                    f"{ts.get('used_mb')} MB used of {ts.get('max_mb')} MB maximum. "
                    "A full tablespace stops DML with ORA-01653/ORA-01652."
                ),
                evidence=[ts],
                solutions=[
                    "Add a datafile or raise MAXSIZE on the autoextensible datafile.",
                    "Reclaim space: drop unused segments, shrink over-allocated tables, purge the "
                    "recycle bin, and archive historical partitions.",
                    "For TEMP/UNDO growth, look for runaway sorts or long-running transactions "
                    "instead of only adding space.",
                ],
                remediation_sql=[
                    f"ALTER TABLESPACE {ts.get('tablespace_name')} ADD DATAFILE SIZE 1G "
                    "AUTOEXTEND ON NEXT 256M MAXSIZE 32G;",
                    "PURGE DBA_RECYCLEBIN;",
                ],
            )
        )
    return findings


def _check_memory(snapshot: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    efficiency = (snapshot.get("memory") or {}).get("efficiency") or {}
    hit = efficiency.get("buffer_cache_hit_pct")
    if hit is not None and hit < BUFFER_CACHE_WARNING_PCT:
        findings.append(
            Finding(
                id="buffer_cache_hit",
                title=f"Buffer cache hit ratio is {hit}%",
                severity="warning",
                category="Memory",
                detail=(
                    "A low hit ratio combined with User I/O waits usually means either an "
                    "undersized cache or SQL doing far more logical/physical reads than needed."
                ),
                evidence=[efficiency],
                solutions=[
                    "Fix the top SQL by disk reads first — most 'cache too small' symptoms are "
                    "really bad access paths.",
                    "If SQL is already tuned, increase DB_CACHE_SIZE / SGA_TARGET.",
                ],
                remediation_sql=["ALTER SYSTEM SET sga_target = <new_size> SCOPE = BOTH;"],
            )
        )
    soft_parse = efficiency.get("soft_parse_pct")
    if soft_parse is not None and soft_parse < SOFT_PARSE_WARNING_PCT:
        findings.append(
            Finding(
                id="hard_parsing",
                title=f"Soft parse ratio is only {soft_parse}%",
                severity="warning",
                category="Memory",
                detail=(
                    "Excessive hard parsing burns CPU and shared pool latches; it is almost always "
                    "caused by literal SQL instead of bind variables."
                ),
                evidence=[efficiency],
                solutions=[
                    "Convert application SQL to bind variables.",
                    "As a stop-gap, set CURSOR_SHARING = FORCE and monitor plan stability.",
                ],
                remediation_sql=["ALTER SYSTEM SET cursor_sharing = FORCE SCOPE = BOTH;"],
            )
        )
    pga = {row.get("name"): row.get("value_mb") for row in (snapshot.get("memory") or {}).get("pga", [])}
    over_allocation = pga.get("over allocation count")
    if over_allocation:
        findings.append(
            Finding(
                id="pga_over_allocation",
                title="PGA target has been over-allocated",
                severity="warning",
                category="Memory",
                detail="Sorts and hash joins spilled beyond the PGA target, forcing temp I/O.",
                evidence=[pga],
                solutions=[
                    "Increase PGA_AGGREGATE_TARGET, or reduce sort/hash volume by tuning the SQL.",
                ],
                remediation_sql=["ALTER SYSTEM SET pga_aggregate_target = <new_size> SCOPE = BOTH;"],
            )
        )
    return findings


def _check_io(snapshot: dict[str, Any]) -> list[Finding]:
    slow = [f for f in (snapshot.get("io") or []) if (f.get("avg_read_ms") or 0) >= SLOW_READ_MS]
    if not slow:
        return []
    worst = slow[0]
    return [
        Finding(
            id="slow_datafile_io",
            title=f"Slow read latency on {len(slow)} datafile(s) (worst {worst.get('avg_read_ms')} ms)",
            severity="warning",
            category="I/O",
            detail=(
                "Average single-block read latency above 20 ms indicates storage saturation or "
                "noisy-neighbour contention."
            ),
            evidence=slow[:5],
            solutions=[
                "Correlate with the top SQL by disk reads — reducing physical reads is cheaper "
                "than buying IOPS.",
                "Spread hot datafiles across devices, or move them to faster storage.",
                "Check the host for CPU steal, queue depth and filesystem cache pressure.",
            ],
        )
    ]


def _check_resources(snapshot: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    for limit in (snapshot.get("overview") or {}).get("resource_limits") or []:
        try:
            maximum = float(limit.get("limit_value"))
        except (TypeError, ValueError):
            continue
        current = float(limit.get("current_utilization") or 0)
        pct = _pct(current, maximum)
        if pct < RESOURCE_LIMIT_WARNING_PCT:
            continue
        findings.append(
            Finding(
                id=f"resource_{limit.get('resource_name')}",
                title=f"{limit.get('resource_name')} utilisation at {pct}% of limit",
                severity="critical" if pct >= 95 else "warning",
                category="Capacity",
                detail=(
                    f"{int(current)} of {int(maximum)} in use. Exhausting this limit produces "
                    "ORA-00020/ORA-00018 and blocks new connections."
                ),
                evidence=[limit],
                solutions=[
                    "Raise the parameter and restart, or reduce demand with connection pooling.",
                    "Look for leaked sessions from application pools that never close connections.",
                ],
                remediation_sql=[
                    f"ALTER SYSTEM SET {limit.get('resource_name')} = <new_value> SCOPE = SPFILE;"
                ],
            )
        )
    return findings


def _check_objects(snapshot: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    invalid = snapshot.get("invalid_objects") or []
    if invalid:
        findings.append(
            Finding(
                id="invalid_objects",
                title=f"{len(invalid)} invalid object(s)",
                severity="warning",
                category="Objects",
                detail="Invalid PL/SQL and views trigger implicit recompilation and library cache locks.",
                evidence=invalid[:10],
                solutions=["Recompile the objects and fix the underlying dependency errors."],
                remediation_sql=["EXEC UTL_RECOMP.RECOMP_PARALLEL(4);"],
            )
        )

    stale = [
        s
        for s in snapshot.get("segments") or []
        if s.get("stale_stats") == "YES" or (s.get("num_rows") is not None and not s.get("last_analyzed"))
    ]
    if stale:
        findings.append(
            Finding(
                id="stale_statistics",
                title=f"{len(stale)} large segment(s) with stale or missing optimizer statistics",
                severity="warning",
                category="Optimizer",
                detail="The optimizer cannot cost plans correctly without fresh statistics.",
                evidence=stale[:10],
                solutions=[
                    "Gather statistics on the affected tables with AUTO_SAMPLE_SIZE and CASCADE.",
                    "Verify the automatic statistics job window is enabled and long enough.",
                ],
                remediation_sql=[
                    "EXEC DBMS_STATS.GATHER_SCHEMA_STATS('<owner>', "
                    "options => 'GATHER AUTO', cascade => TRUE);"
                ],
            )
        )
    return findings


def _check_long_calls(snapshot: dict[str, Any]) -> list[Finding]:
    long_calls = [
        s
        for s in snapshot.get("active_sessions") or []
        if (s.get("seconds_in_call") or 0) >= LONG_CALL_SECONDS
    ]
    if not long_calls:
        return []
    return [
        Finding(
            id="long_running_sessions",
            title=f"{len(long_calls)} session(s) active for more than {LONG_CALL_SECONDS}s",
            severity="warning",
            category="Sessions",
            detail="Long active calls monopolise CPU, undo and temp space.",
            evidence=long_calls[:10],
            solutions=[
                "Pull the SQL id and plan for each session and tune the statement.",
                "Consider Resource Manager to cap runaway ad-hoc queries.",
            ],
            remediation_sql=[
                "SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY_CURSOR('<sql_id>', NULL, 'ALLSTATS LAST'));"
            ],
        )
    ]


RULES = (
    _check_blocking,
    _check_waits,
    _check_sql,
    _check_space,
    _check_memory,
    _check_io,
    _check_resources,
    _check_objects,
    _check_long_calls,
)


def diagnose(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Evaluate every rule against a snapshot and return a report."""
    findings: list[Finding] = []
    for rule in RULES:
        try:
            findings.extend(rule(snapshot))
        except Exception as exc:  # a broken rule must not kill the whole report
            findings.append(
                Finding(
                    id=f"rule_error_{rule.__name__}",
                    title=f"Diagnostic rule {rule.__name__} failed",
                    severity="info",
                    category="Agent",
                    detail=str(exc),
                )
            )
    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 3), f.category))

    counts = {level: sum(1 for f in findings if f.severity == level) for level in SEVERITY_ORDER}
    if counts["critical"]:
        status, headline = "critical", "Immediate action required"
    elif counts["warning"]:
        status, headline = "degraded", "Performance risks detected"
    else:
        status, headline = "healthy", "No performance problems detected"

    instance = (snapshot.get("overview") or {}).get("instance") or {}
    sessions = (snapshot.get("overview") or {}).get("sessions") or {}
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": status,
        "headline": headline,
        "counts": counts,
        "instance": instance,
        "sessions": sessions,
        "findings": [f.to_dict() for f in findings],
        "snapshot": snapshot,
    }
