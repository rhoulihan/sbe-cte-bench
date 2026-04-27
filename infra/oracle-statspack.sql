-- Statspack installation. Free Edition does not include AWR; Statspack is the
-- equivalent free utility, shipped with Oracle since 8i.
--
-- Idempotent: skips installation if PERFSTAT user already exists.

ALTER SESSION SET CONTAINER = FREEPDB1;

DECLARE
  perfstat_exists NUMBER;
BEGIN
  SELECT COUNT(*) INTO perfstat_exists FROM dba_users WHERE username = 'PERFSTAT';
  IF perfstat_exists > 0 THEN
    DBMS_OUTPUT.PUT_LINE('PERFSTAT already installed; skipping');
    RETURN;
  END IF;
END;
/

-- Tablespace for Statspack snapshots.
CREATE TABLESPACE PERFSTAT
  DATAFILE '/opt/oracle/oradata/FREE/FREEPDB1/perfstat.dbf'
  SIZE 64M AUTOEXTEND ON NEXT 64M MAXSIZE 1G
  EXTENT MANAGEMENT LOCAL
  SEGMENT SPACE MANAGEMENT AUTO;

-- Run the Statspack installation script. Need to be SYS for spcreate.sql.
-- gvenzl image runs initdb scripts as SYS, so this works.
DEFINE perfstat_password = 'BenchPass2026'
DEFINE default_tablespace = 'PERFSTAT'
DEFINE temporary_tablespace = 'TEMP'

@?/rdbms/admin/spcreate.sql

EXIT
