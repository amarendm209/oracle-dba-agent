-- Minimum read-only privileges the DBA agent needs.
-- Run as SYSDBA in the pluggable database that hosts the agent account.
GRANT CREATE SESSION TO dba_agent;
GRANT SELECT ANY DICTIONARY TO dba_agent;
GRANT SELECT_CATALOG_ROLE TO dba_agent;
