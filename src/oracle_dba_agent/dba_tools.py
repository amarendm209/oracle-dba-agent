"""Oracle diagnostic collectors exposed as MCP tools.

Every function is read-only and uses dynamic performance views that are available
without the Diagnostics Pack (no ASH/AWR views), so the agent is safe to point at
Standard Edition, Express Edition and unlicensed Enterprise Edition instances.
"""

from __future__ import annotations

from typing import Any

from .db import query, scalar

Rows = list[dict[str, Any]]


def database_overview() -> dict[str, Any]:
    """Instance identity, version, uptime, status and current session counts."""
    instance = query(
        """
        SELECT instance_name, host_name, version_full AS version, status, database_status,
               TO_CHAR(startup_time, 'YYYY-MM-DD HH24:MI:SS') AS startup_time,
               ROUND((SYSDATE - startup_time) * 24, 2) AS uptime_hours
          FROM v$instance
        """
    )
    sessions = query(
        """
        SELECT COUNT(*) AS total_sessions,
               SUM(CASE WHEN status = 'ACTIVE' THEN 1 ELSE 0 END) AS active_sessions,
               SUM(CASE WHEN blocking_session IS NOT NULL THEN 1 ELSE 0 END) AS blocked_sessions
          FROM v$session
         WHERE type = 'USER'
        """
    )
    limits = query(
        """
        SELECT resource_name, current_utilization, max_utilization, limit_value
          FROM v$resource_limit
         WHERE resource_name IN ('processes', 'sessions', 'transactions')
        """
    )
    return {
        "instance": instance[0] if instance else {},
        "sessions": sessions[0] if sessions else {},
        "resource_limits": limits,
    }


def active_sessions(limit: int = 25) -> Rows:
    """Currently active user sessions with their wait event, SQL id and elapsed time."""
    return query(
        """
        SELECT s.sid, s.serial# AS serial_no, s.username, s.machine, s.program, s.module,
               s.status, s.sql_id, s.event, s.wait_class, s.seconds_in_wait,
               s.blocking_session, s.last_call_et AS seconds_in_call
          FROM v$session s
         WHERE s.type = 'USER'
           AND s.status = 'ACTIVE'
           AND s.sid <> SYS_CONTEXT('USERENV', 'SID')
         ORDER BY s.last_call_et DESC
        """,
        limit=limit,
    )


def blocking_sessions(limit: int = 25) -> Rows:
    """Sessions that are blocking other sessions, with the blocked waiter details."""
    return query(
        """
        SELECT b.sid AS blocker_sid, b.serial# AS blocker_serial, b.username AS blocker_user,
               b.program AS blocker_program, b.sql_id AS blocker_sql_id, b.status AS blocker_status,
               w.sid AS waiter_sid, w.username AS waiter_user, w.event AS waiter_event,
               w.seconds_in_wait AS waiter_seconds
          FROM v$session w
          JOIN v$session b ON b.sid = w.blocking_session
         WHERE w.blocking_session IS NOT NULL
         ORDER BY w.seconds_in_wait DESC
        """,
        limit=limit,
    )


def top_wait_events(limit: int = 15) -> Rows:
    """Top foreground wait events since instance startup, excluding idle classes."""
    return query(
        """
        SELECT event, wait_class, total_waits, total_timeouts,
               ROUND(time_waited_micro / 1e6, 2) AS time_waited_seconds,
               ROUND(average_wait * 10, 2) AS avg_wait_ms
          FROM v$system_event
         WHERE wait_class NOT IN ('Idle')
         ORDER BY time_waited_micro DESC
        """,
        limit=limit,
    )


def current_waits(limit: int = 20) -> Rows:
    """What sessions are waiting on right now, aggregated by event."""
    return query(
        """
        SELECT event, wait_class, COUNT(*) AS sessions_waiting,
               ROUND(AVG(seconds_in_wait), 1) AS avg_seconds_in_wait,
               MAX(seconds_in_wait) AS max_seconds_in_wait
          FROM v$session
         WHERE type = 'USER'
           AND state = 'WAITING'
           AND wait_class <> 'Idle'
         GROUP BY event, wait_class
         ORDER BY sessions_waiting DESC, max_seconds_in_wait DESC
        """,
        limit=limit,
    )


def top_sql(order_by: str = "elapsed", limit: int = 15) -> Rows:
    """Most expensive cached SQL ordered by elapsed time, CPU, buffer gets or executions."""
    order_column = {
        "elapsed": "elapsed_time",
        "cpu": "cpu_time",
        "gets": "buffer_gets",
        "reads": "disk_reads",
        "executions": "executions",
    }.get(order_by.lower())
    if order_column is None:
        raise ValueError("order_by must be one of: elapsed, cpu, gets, reads, executions")
    return query(
        f"""
        SELECT sql_id, plan_hash_value, executions,
               ROUND(elapsed_time / 1e6, 2) AS elapsed_seconds,
               ROUND(cpu_time / 1e6, 2) AS cpu_seconds,
               ROUND(elapsed_time / 1e6 / GREATEST(executions, 1), 4) AS elapsed_seconds_per_exec,
               buffer_gets, disk_reads, rows_processed,
               ROUND(buffer_gets / GREATEST(executions, 1), 1) AS gets_per_exec,
               parsing_schema_name, SUBSTR(sql_text, 1, 400) AS sql_text
          FROM v$sqlarea
         WHERE parsing_schema_name NOT IN ('SYS', 'SYSTEM', 'DBSNMP')
         ORDER BY {order_column} DESC
        """,  # noqa: S608 - order_column comes from a fixed allow-list
        limit=limit,
    )


def sql_details(sql_id: str) -> dict[str, Any]:
    """Full text, statistics and execution plan for one SQL id."""
    stats = query(
        """
        SELECT sql_id, plan_hash_value, executions, parsing_schema_name,
               ROUND(elapsed_time / 1e6, 2) AS elapsed_seconds,
               ROUND(cpu_time / 1e6, 2) AS cpu_seconds,
               buffer_gets, disk_reads, rows_processed, sql_fulltext
          FROM v$sqlarea
         WHERE sql_id = :sql_id
        """,
        {"sql_id": sql_id},
        limit=1,
    )
    plan = query(
        """
        SELECT id, LPAD(' ', depth) || operation || ' ' || NVL(options, '') AS operation,
               object_name, cardinality, bytes, cost, access_predicates, filter_predicates
          FROM v$sql_plan
         WHERE sql_id = :sql_id
         ORDER BY child_number, id
        """,
        {"sql_id": sql_id},
        limit=200,
    )
    return {"sql_id": sql_id, "statistics": stats[0] if stats else {}, "plan": plan}


def tablespace_usage(limit: int = 30) -> Rows:
    """Space usage per tablespace including autoextensible headroom."""
    return query(
        """
        SELECT tablespace_name,
               ROUND(used_space * (SELECT value FROM v$parameter WHERE name = 'db_block_size')
                     / 1024 / 1024, 1) AS used_mb,
               ROUND(tablespace_size * (SELECT value FROM v$parameter WHERE name = 'db_block_size')
                     / 1024 / 1024, 1) AS max_mb,
               ROUND(used_percent, 2) AS used_percent
          FROM dba_tablespace_usage_metrics
         ORDER BY used_percent DESC
        """,
        limit=limit,
    )


def memory_and_cache_health() -> dict[str, Any]:
    """Buffer cache / library cache / dictionary cache efficiency and memory pool sizes."""
    ratios = query(
        """
        SELECT
          ROUND(100 * (1 - (SUM(CASE WHEN name = 'physical reads cache' THEN value END) /
                NULLIF(SUM(CASE WHEN name IN ('db block gets from cache',
                                              'consistent gets from cache')
                               THEN value END), 0))), 2) AS buffer_cache_hit_pct,
          ROUND(100 * (1 - (SUM(CASE WHEN name = 'parse count (hard)' THEN value END) /
                NULLIF(SUM(CASE WHEN name = 'parse count (total)' THEN value END), 0))), 2)
                AS soft_parse_pct,
          SUM(CASE WHEN name = 'user commits' THEN value END) AS user_commits,
          SUM(CASE WHEN name = 'execute count' THEN value END) AS execute_count
        FROM v$sysstat
        """
    )
    pools = query(
        """
        SELECT pool, ROUND(SUM(bytes) / 1024 / 1024, 1) AS size_mb
          FROM v$sgastat
         WHERE pool IS NOT NULL
         GROUP BY pool
         ORDER BY size_mb DESC
        """
    )
    pga = query(
        """
        SELECT name, ROUND(value / 1024 / 1024, 1) AS value_mb
          FROM v$pgastat
         WHERE name IN ('total PGA allocated', 'total PGA inuse', 'aggregate PGA target parameter',
                        'over allocation count')
        """
    )
    library = query(
        """
        SELECT namespace, gets, gethits, ROUND(gethitratio * 100, 2) AS get_hit_pct, reloads
          FROM v$librarycache
         WHERE namespace IN ('SQL AREA', 'TABLE/PROCEDURE', 'BODY', 'TRIGGER')
        """
    )
    return {
        "efficiency": ratios[0] if ratios else {},
        "sga_pools_mb": pools,
        "pga": pga,
        "library_cache": library,
    }


def io_hotspots(limit: int = 15) -> Rows:
    """Datafiles with the highest physical I/O and read latency."""
    return query(
        """
        SELECT df.tablespace_name, df.file_name, fs.phyrds AS physical_reads,
               fs.phywrts AS physical_writes,
               ROUND(fs.readtim / GREATEST(fs.phyrds, 1) * 10, 2) AS avg_read_ms,
               ROUND(fs.writetim / GREATEST(fs.phywrts, 1) * 10, 2) AS avg_write_ms
          FROM v$filestat fs
          JOIN dba_data_files df ON df.file_id = fs.file#
         ORDER BY fs.phyrds + fs.phywrts DESC
        """,
        limit=limit,
    )


def long_running_operations(limit: int = 15) -> Rows:
    """Operations reporting progress through v$session_longops."""
    return query(
        """
        SELECT sid, serial# AS serial_no, username, opname, target,
               ROUND(sofar / GREATEST(totalwork, 1) * 100, 1) AS percent_complete,
               elapsed_seconds, time_remaining AS seconds_remaining, sql_id
          FROM v$session_longops
         WHERE totalwork > 0
           AND sofar < totalwork
         ORDER BY elapsed_seconds DESC
        """,
        limit=limit,
    )


def segment_health(limit: int = 20) -> Rows:
    """Largest segments plus their statistics freshness (stale stats hurt plans)."""
    return query(
        """
        SELECT s.owner, s.segment_name, s.segment_type,
               ROUND(s.bytes / 1024 / 1024, 1) AS size_mb,
               t.num_rows, t.stale_stats,
               TO_CHAR(t.last_analyzed, 'YYYY-MM-DD HH24:MI') AS last_analyzed
          FROM dba_segments s
          LEFT JOIN dba_tab_statistics t
            ON t.owner = s.owner AND t.table_name = s.segment_name AND t.object_type = 'TABLE'
         WHERE s.owner NOT IN ('SYS', 'SYSTEM', 'DBSNMP', 'AUDSYS', 'XDB', 'WMSYS', 'OUTLN')
         ORDER BY s.bytes DESC
        """,
        limit=limit,
    )


def invalid_objects(limit: int = 25) -> Rows:
    """Invalid schema objects, which typically cause recompilation storms."""
    return query(
        """
        SELECT owner, object_name, object_type,
               TO_CHAR(last_ddl_time, 'YYYY-MM-DD HH24:MI') AS last_ddl_time
          FROM dba_objects
         WHERE status = 'INVALID'
           AND owner NOT IN ('SYS', 'SYSTEM')
         ORDER BY owner, object_name
        """,
        limit=limit,
    )


def parameter_check(names: list[str] | None = None) -> Rows:
    """Values of performance-relevant initialization parameters."""
    default = [
        "cursor_sharing",
        "db_block_size",
        "memory_target",
        "open_cursors",
        "optimizer_mode",
        "pga_aggregate_target",
        "processes",
        "sessions",
        "sga_target",
        "statistics_level",
    ]
    wanted = [n.lower() for n in (names or default)]
    binds = {f"p{i}": name for i, name in enumerate(wanted)}
    placeholders = ", ".join(f":{key}" for key in binds)
    return query(
        f"SELECT name, value, isdefault FROM v$parameter WHERE name IN ({placeholders}) ORDER BY name",  # noqa: S608 - placeholders are bind variables
        binds,
        limit=len(wanted),
    )


def run_readonly_query(sql: str, limit: int = 50) -> Rows:
    """Escape hatch: run an ad-hoc read-only query (SELECT/WITH only)."""
    return query(sql, limit=limit)


def health_snapshot() -> dict[str, Any]:
    """One-shot collection of every diagnostic area, used by the diagnosis engine."""
    return {
        "overview": database_overview(),
        "active_sessions": active_sessions(),
        "blocking_sessions": blocking_sessions(),
        "top_wait_events": top_wait_events(),
        "current_waits": current_waits(),
        "top_sql": top_sql(),
        "tablespaces": tablespace_usage(),
        "memory": memory_and_cache_health(),
        "io": io_hotspots(),
        "long_operations": long_running_operations(),
        "segments": segment_health(),
        "invalid_objects": invalid_objects(),
        "parameters": parameter_check(),
    }


def ping() -> dict[str, Any]:
    """Cheap connectivity check against the target database."""
    return {"ok": scalar("SELECT 1 FROM dual") == 1}
