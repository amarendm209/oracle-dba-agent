from oracle_dba_agent.diagnosis import diagnose


def _snapshot(**overrides):
    base = {
        "overview": {
            "instance": {"instance_name": "FREE", "uptime_hours": 3},
            "sessions": {"total_sessions": 5, "active_sessions": 2, "blocked_sessions": 0},
            "resource_limits": [
                {"resource_name": "processes", "current_utilization": 10, "limit_value": "100"}
            ],
        },
        "active_sessions": [],
        "blocking_sessions": [],
        "top_wait_events": [],
        "current_waits": [],
        "top_sql": [],
        "tablespaces": [],
        "memory": {"efficiency": {"buffer_cache_hit_pct": 99.0, "soft_parse_pct": 99.0}, "pga": []},
        "io": [],
        "long_operations": [],
        "segments": [],
        "invalid_objects": [],
        "parameters": [],
    }
    base.update(overrides)
    return base


def test_healthy_snapshot_has_no_findings():
    report = diagnose(_snapshot())
    assert report["status"] == "healthy"
    assert report["findings"] == []


def test_blocking_sessions_are_critical():
    report = diagnose(
        _snapshot(
            blocking_sessions=[
                {"blocker_sid": 10, "blocker_serial": 5, "waiter_sid": 20, "waiter_seconds": 120}
            ]
        )
    )
    assert report["status"] == "critical"
    finding = next(f for f in report["findings"] if f["id"] == "blocking_sessions")
    assert finding["severity"] == "critical"
    assert finding["solutions"]
    assert "KILL SESSION '10,5'" in finding["remediation_sql"][1]


def test_full_tablespace_and_expensive_sql_are_reported():
    report = diagnose(
        _snapshot(
            tablespaces=[{"tablespace_name": "USERS", "used_percent": 97.0, "used_mb": 970, "max_mb": 1000}],
            top_sql=[
                {
                    "sql_id": "abc123",
                    "executions": 100,
                    "elapsed_seconds": 900.0,
                    "elapsed_seconds_per_exec": 9.0,
                    "gets_per_exec": 5000,
                    "disk_reads": 42,
                    "parsing_schema_name": "APP",
                }
            ],
        )
    )
    ids = {f["id"] for f in report["findings"]}
    assert "tablespace_USERS" in ids
    assert "sql_abc123" in ids
    assert report["counts"]["critical"] == 2


def test_low_cache_and_parse_ratios_warn():
    report = diagnose(
        _snapshot(memory={"efficiency": {"buffer_cache_hit_pct": 70.0, "soft_parse_pct": 40.0}, "pga": []})
    )
    ids = {f["id"] for f in report["findings"]}
    assert {"buffer_cache_hit", "hard_parsing"} <= ids
    assert report["status"] == "degraded"


def test_rule_failure_is_isolated():
    report = diagnose(_snapshot(tablespaces="not-a-list"))
    assert any(f["id"].startswith("rule_error") for f in report["findings"])
