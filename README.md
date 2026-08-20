# AI Oracle DBA Agent

Real-time Oracle performance diagnosis, delivered through an MCP toolset and a web dashboard.

- **FastMCP server** (`oracle_dba_agent.mcp_server`) — 18 read-only Oracle DBA tools
  (sessions, blockers, waits, top SQL and plans, tablespaces, memory, I/O, statistics,
  parameters, ad-hoc query).
- **Agent** (`oracle_dba_agent.agent`) — never queries the database directly; it drives the
  MCP tools, in-process by default or against a remote MCP server via `DBA_MCP_URL`.
- **Diagnosis engine** (`oracle_dba_agent.diagnosis`) — rules that turn a raw snapshot into
  prioritised findings, each with an explanation, evidence and concrete solutions/remediation SQL.
- **Web dashboard** (`oracle_dba_agent.web.app`) — auto-refreshing page showing status, findings,
  solutions and the underlying evidence tables; every MCP tool can also be run ad hoc from the page.

Credentials are read from the environment (Devin secrets, Kubernetes secrets, `.env` for local
work) — nothing is hardcoded, and the data layer rejects anything that is not a `SELECT`/`WITH`.

## Quick start

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

# local Oracle for development
docker compose up -d oracle          # gvenzl/oracle-free:23-slim, ~2 min to open

cp .env.example .env                 # then set ORACLE_PASSWORD from your secret store
dba-web                              # http://localhost:8000
```

Grant the agent account read-only dictionary access once (see `scripts/grant_dba_agent.sql`):

```sql
GRANT CREATE SESSION, SELECT ANY DICTIONARY TO dba_agent;
GRANT SELECT_CATALOG_ROLE TO dba_agent;
```

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `ORACLE_USER` / `ORACLE_PASSWORD` | – | agent database account (from your secret store) |
| `ORACLE_HOST` / `ORACLE_PORT` / `ORACLE_SERVICE` | `localhost` / `1521` / `FREEPDB1` | connection target |
| `ORACLE_DSN` | – | full DSN / TNS alias, overrides host+port+service |
| `DBA_QUERY_TIMEOUT` | `30` | per-query call timeout (seconds) |
| `DBA_MCP_URL` | – | use a remote FastMCP server instead of the in-process one |
| `DBA_WEB_HOST` / `DBA_WEB_PORT` | `0.0.0.0` / `8000` | dashboard bind address |

## Running the MCP server standalone

```bash
dba-mcp                                   # stdio, for MCP clients (Claude Desktop, Devin, IDEs)
dba-mcp --transport http --port 8765      # HTTP, then set DBA_MCP_URL=http://127.0.0.1:8765/mcp
```

Example stdio client config:

```json
{
  "mcpServers": {
    "oracle-dba": {
      "command": "dba-mcp",
      "env": { "ORACLE_USER": "dba_agent", "ORACLE_PASSWORD": "...", "ORACLE_DSN": "host:1521/SERVICE" }
    }
  }
}
```

## Tools

| Tool | What it answers |
| --- | --- |
| `oracle_ping` | can the agent reach the database? |
| `oracle_overview` | instance, uptime, session counts, resource limits |
| `oracle_active_sessions` | who is active, on what SQL, waiting on what |
| `oracle_blocking_sessions` | lock holders and their victims |
| `oracle_current_waits` | live wait profile, aggregated by event |
| `oracle_top_wait_events` | cumulative non-idle wait profile |
| `oracle_top_sql` | worst SQL by elapsed / cpu / gets / reads / executions |
| `oracle_sql_details` | full text, stats and execution plan for one `sql_id` |
| `oracle_tablespace_usage` | space pressure per tablespace |
| `oracle_memory_health` | buffer/library cache hit ratios, SGA pools, PGA |
| `oracle_io_hotspots` | datafile read/write latency |
| `oracle_long_operations` | in-flight long operations with progress |
| `oracle_segment_health` | biggest segments and statistics freshness |
| `oracle_invalid_objects` | invalid PL/SQL and views |
| `oracle_parameters` | performance-relevant init parameters |
| `oracle_run_query` | ad-hoc read-only query |
| `oracle_health_snapshot` | everything above in one call |
| `oracle_diagnose` | snapshot + findings + solutions |

All tools use views available without the Diagnostics Pack (no ASH/AWR), so they are safe on
Standard, Express and unlicensed Enterprise editions.

## Diagnostic rules

Blocking chains · live wait contention · dominant cumulative wait event · expensive SQL
(elapsed per execution, buffer gets per execution) · tablespace pressure · buffer cache hit ratio ·
hard parsing · PGA over-allocation · slow datafile I/O · process/session limit exhaustion ·
invalid objects · stale optimizer statistics · long-running sessions.

Each finding carries a severity (`critical` / `warning` / `info`), the evidence rows that
triggered it, prose solutions, and copy-pasteable remediation SQL. Thresholds live at the top of
`src/oracle_dba_agent/diagnosis.py` and are meant to be tuned per environment.

## HTTP API

| Endpoint | Purpose |
| --- | --- |
| `GET /api/diagnose` | full report: status, counts, findings, snapshot |
| `GET /api/tools` | MCP tool catalogue |
| `POST /api/tools/{name}` | run a single MCP tool with a JSON argument body |
| `GET /api/health` | connectivity check |
| `GET /api/target` | which instance/user/MCP server is in use |

## Tests

```bash
pytest        # rule engine, MCP tool catalogue, read-only guard (no database needed)
ruff check .
```
