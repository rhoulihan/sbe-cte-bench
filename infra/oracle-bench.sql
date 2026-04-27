-- Oracle 26ai Free init script — runs once at container first-boot via the
-- gvenzl/oracle-free `/container-entrypoint-initdb.d/` hook.
--
-- Creates BENCH_DATA tablespace sized to fit within the 12 GB user-data cap,
-- the BENCH user with appropriate grants, and BENCH defaults.

ALTER SESSION SET CONTAINER = FREEPDB1;

-- Tablespace for benchmark data. Sized at 10 GB initial + autoextend with a
-- maxsize of 11 GB so we stay below the 12 GB Free cap with headroom for
-- system segments.
CREATE TABLESPACE BENCH_DATA
  DATAFILE '/opt/oracle/oradata/FREE/FREEPDB1/bench_data.dbf'
  SIZE 256M AUTOEXTEND ON NEXT 256M MAXSIZE 11G
  EXTENT MANAGEMENT LOCAL
  SEGMENT SPACE MANAGEMENT AUTO;

-- BENCH user. APP_USER + APP_USER_PASSWORD env vars from compose.yaml create
-- a similarly-named user; this script ensures it has the right grants and
-- default tablespace regardless of the order.
DECLARE
  user_exists NUMBER;
BEGIN
  SELECT COUNT(*) INTO user_exists FROM dba_users WHERE username = 'BENCH';
  IF user_exists = 0 THEN
    EXECUTE IMMEDIATE 'CREATE USER BENCH IDENTIFIED BY "BenchPass2026"';
  END IF;
END;
/

ALTER USER BENCH DEFAULT TABLESPACE BENCH_DATA;
ALTER USER BENCH QUOTA UNLIMITED ON BENCH_DATA;

GRANT CREATE SESSION         TO BENCH;
GRANT CREATE TABLE           TO BENCH;
GRANT CREATE VIEW            TO BENCH;
GRANT CREATE PROCEDURE       TO BENCH;
GRANT CREATE SEQUENCE        TO BENCH;
GRANT CREATE TRIGGER         TO BENCH;
GRANT CREATE TYPE            TO BENCH;
GRANT CREATE MATERIALIZED VIEW TO BENCH;

-- Allow access to system views needed for instrumentation capture.
GRANT SELECT ON v_$mystat       TO BENCH;
GRANT SELECT ON v_$statname     TO BENCH;
GRANT SELECT ON v_$sql_workarea_active TO BENCH;
GRANT SELECT ON v_$pgastat      TO BENCH;
GRANT SELECT ON v_$session      TO BENCH;
GRANT SELECT ON v_$sql          TO BENCH;
GRANT SELECT ON dba_hist_active_sess_history TO BENCH;

-- CBO-relevant settings, fresh and freely-replannable per scenario.
ALTER SESSION SET CONTAINER = FREEPDB1;
ALTER SYSTEM SET RESULT_CACHE_MODE = MANUAL SCOPE=BOTH;
ALTER SYSTEM SET OPTIMIZER_CAPTURE_SQL_PLAN_BASELINES = FALSE SCOPE=BOTH;

EXIT
