import glob
import os
import sqlite3
import tempfile
from collections import Counter

import pandas as pd

from common.utils import setup_logging


logger = setup_logging("stats-cache")
FAILURE_VERBS = ("preparing", "executing", "restoring")
IDENTITY_COLUMNS = (
    "runid", "job_id", "backend", "system", "scenario", "attempt", "name",
)
DIMENSION_COLUMNS = (
    "start_date", "backend", "system", "scenario", "attempt", "name",
)
FILTER_COLUMNS = {"name", "backend", "system", "scenario", "attempt"}
SCHEMA_VERSION = 2


def _value(value, default=""):
    try:
        return default if value is None or pd.isna(value) else str(value)
    except (TypeError, ValueError):
        return str(value)


def _date_value(value):
    timestamp = pd.to_datetime(value, errors="coerce", utc=True)
    return None if pd.isna(timestamp) else timestamp.date().isoformat()


def _create_staging_schema(connection):
    identity = ", ".join(f"{column} TEXT NOT NULL" for column in IDENTITY_COLUMNS)
    identity_key = ", ".join((*IDENTITY_COLUMNS, "start_date"))
    connection.executescript(f"""
        CREATE TABLE outcomes (
            {identity},
            start_date TEXT NOT NULL,
            executing_pass INTEGER NOT NULL,
            fail_preparing INTEGER NOT NULL,
            fail_executing INTEGER NOT NULL,
            fail_restoring INTEGER NOT NULL,
            PRIMARY KEY ({identity_key})
        ) WITHOUT ROWID;
        CREATE TABLE outcome_sources (
            source TEXT NOT NULL,
            {identity},
            start_date TEXT NOT NULL,
            executing_pass INTEGER NOT NULL,
            fail_preparing INTEGER NOT NULL,
            fail_executing INTEGER NOT NULL,
            fail_restoring INTEGER NOT NULL,
            PRIMARY KEY (source, {identity_key})
        ) WITHOUT ROWID;
    """)


def _load_processed(connection, directory):
    identity_key = ", ".join((*IDENTITY_COLUMNS, "start_date"))
    insert_outcome = f"""
        INSERT INTO outcomes VALUES ({", ".join("?" for _ in range(12))})
        ON CONFLICT ({identity_key}) DO UPDATE SET
            executing_pass = MAX(executing_pass, excluded.executing_pass),
            fail_preparing = MAX(fail_preparing, excluded.fail_preparing),
            fail_executing = MAX(fail_executing, excluded.fail_executing),
            fail_restoring = MAX(fail_restoring, excluded.fail_restoring)
    """
    insert_source = f"""
        INSERT INTO outcome_sources VALUES ({", ".join("?" for _ in range(13))})
        ON CONFLICT (source, {identity_key}) DO UPDATE SET
            executing_pass = MAX(executing_pass, excluded.executing_pass),
            fail_preparing = MAX(fail_preparing, excluded.fail_preparing),
            fail_executing = MAX(fail_executing, excluded.fail_executing),
            fail_restoring = MAX(fail_restoring, excluded.fail_restoring)
    """

    files = glob.glob(os.path.join(directory, "*.ts"))
    for file_index, path in enumerate(files, start=1):
        try:
            frame = pd.read_csv(path)
            outcomes = {}
            for item in frame.to_dict(orient="records"):
                verb = _value(item.get("verb"))
                success = pd.to_numeric(item.get("success"), errors="coerce")
                passed = verb == "executing" and success == 1
                failed = verb in FAILURE_VERBS and success == 0
                if not passed and not failed:
                    continue

                start_date = _date_value(item.get("start"))
                if start_date is None:
                    continue
                key = tuple(_value(item.get(column)) for column in IDENTITY_COLUMNS)
                key = (*key, start_date)
                flags = outcomes.setdefault(key, [0, 0, 0, 0])
                flags[0] = max(flags[0], int(passed))
                if failed:
                    flags[1 + FAILURE_VERBS.index(verb)] = 1

            rows = [(*key, *flags) for key, flags in outcomes.items()]
            connection.executemany(insert_outcome, rows)
            source = os.path.basename(path)
            connection.executemany(
                insert_source,
                ((source, *key, *flags) for key, flags in outcomes.items()),
            )
        except Exception as error:
            logger.error(f"Error aggregating stats input {path}: {error}")

        if file_index % 500 == 0 or file_index == len(files):
            logger.info(
                "Stats aggregation progress: %d/%d processed files.",
                file_index,
                len(files),
            )


def _materialize_processed_stats(connection):
    dimensions = ", ".join(DIMENSION_COLUMNS)
    aggregates = """
        SUM(CASE WHEN executing_pass = 1 AND
                      fail_preparing = 0 AND fail_executing = 0 AND
                      fail_restoring = 0 THEN 1 ELSE 0 END) AS pass,
        SUM(CASE WHEN fail_preparing = 1 OR fail_executing = 1 OR
                      fail_restoring = 1 THEN 1 ELSE 0 END) AS fail,
        SUM(CASE WHEN executing_pass = 1 AND fail_preparing = 0
                 THEN 1 ELSE 0 END) AS pass_preparing,
        SUM(fail_preparing) AS fail_preparing,
        SUM(CASE WHEN executing_pass = 1 AND fail_executing = 0
                 THEN 1 ELSE 0 END) AS pass_executing,
        SUM(fail_executing) AS fail_executing,
        SUM(CASE WHEN executing_pass = 1 AND fail_restoring = 0
                 THEN 1 ELSE 0 END) AS pass_restoring,
        SUM(fail_restoring) AS fail_restoring
    """
    source_aggregates = aggregates.replace("SUM(", "MAX(")
    connection.executescript(f"""
        CREATE TABLE daily_stats AS
            SELECT {dimensions}, {aggregates}
            FROM outcomes
            GROUP BY {dimensions};

        CREATE TABLE source_stats AS
            SELECT source, {dimensions},
                   {source_aggregates}
            FROM outcome_sources
            GROUP BY source, {dimensions};

        DROP TABLE outcome_sources;
        DROP TABLE outcomes;
    """)


def _load_aborted(connection, directory):
    counts = Counter()
    sources = set()
    files = glob.glob(os.path.join(directory, "*.csv"))
    for path in files:
        try:
            frame = pd.read_csv(path)
            source = os.path.basename(path)
            for item in frame.to_dict(orient="records"):
                if _value(item.get("aborted")).strip().lower() != "true":
                    continue
                start_date = _date_value(item.get("start"))
                if start_date is None:
                    continue
                name = _value(item.get("name"))
                variant = _value(item.get("variant"))
                dimensions = (
                    start_date,
                    _value(item.get("backend"), "unknown"),
                    _value(item.get("system"), "unknown"),
                    _value(item.get("scenario")),
                    _value(item.get("attempt")),
                    f"{name}:{variant}" if variant else name,
                    _value(item.get("verb")),
                )
                counts[dimensions] += 1
                sources.add((source, *dimensions))
        except Exception as error:
            logger.error(f"Error aggregating aborted stats input {path}: {error}")

    connection.executescript("""
        CREATE TABLE aborted_stats (
            start_date TEXT NOT NULL,
            backend TEXT NOT NULL,
            system TEXT NOT NULL,
            scenario TEXT NOT NULL,
            attempt TEXT NOT NULL,
            name TEXT NOT NULL,
            verb TEXT NOT NULL,
            aborted INTEGER NOT NULL
        );
        CREATE TABLE aborted_sources (
            source TEXT NOT NULL,
            start_date TEXT NOT NULL,
            backend TEXT NOT NULL,
            system TEXT NOT NULL,
            scenario TEXT NOT NULL,
            attempt TEXT NOT NULL,
            name TEXT NOT NULL,
            verb TEXT NOT NULL,
            PRIMARY KEY (source, start_date, backend, system, scenario,
                         attempt, name, verb)
        ) WITHOUT ROWID;
    """)
    connection.executemany(
        "INSERT INTO aborted_stats VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ((*dimensions, count) for dimensions, count in counts.items()),
    )
    connection.executemany(
        "INSERT INTO aborted_sources VALUES (?, ?, ?, ?, ?, ?, ?, ?)", sources
    )


def _create_indexes(connection):
    connection.executescript(f"""
        CREATE INDEX daily_stats_filters
            ON daily_stats (scenario, backend, system, start_date);
        CREATE INDEX daily_stats_name ON daily_stats (name);
        CREATE INDEX source_stats_filters
            ON source_stats (scenario, backend, system, start_date);
        CREATE INDEX aborted_stats_filters
            ON aborted_stats (scenario, backend, system, start_date);
        CREATE INDEX aborted_stats_name ON aborted_stats (name);
        CREATE INDEX aborted_sources_filters
            ON aborted_sources (scenario, backend, system, start_date);
        PRAGMA user_version = {SCHEMA_VERSION};
        ANALYZE;
    """)


def build_stats_snapshot(ts_dir, archive_dir, snapshot_path):
    """Build an aggregate SQLite stats snapshot and atomically replace it."""
    os.makedirs(os.path.dirname(snapshot_path), exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix="stats-snapshot-", suffix=".sqlite3", dir=os.path.dirname(snapshot_path)
    )
    os.close(descriptor)
    try:
        connection = sqlite3.connect(temporary_path)
        try:
            connection.execute("PRAGMA journal_mode=OFF")
            connection.execute("PRAGMA synchronous=OFF")
            _create_staging_schema(connection)
            _load_processed(connection, ts_dir)
            _materialize_processed_stats(connection)
            _load_aborted(connection, archive_dir)
            _create_indexes(connection)
            connection.commit()
            connection.execute("VACUUM")
        finally:
            connection.close()
        os.replace(temporary_path, snapshot_path)
        logger.info(f"Stats snapshot saved to {snapshot_path}")
        return True
    except Exception as error:
        logger.error(f"Failed to build stats snapshot: {error}", exc_info=True)
        return False
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)


def snapshot_available(snapshot_path):
    if not os.path.isfile(snapshot_path):
        return False
    try:
        with sqlite3.connect(snapshot_path) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
        return version == SCHEMA_VERSION
    except sqlite3.Error:
        return False


def _where_clause(filters, table, excluded=()):
    clauses = []
    parameters = []
    for column in FILTER_COLUMNS:
        value = filters.get(column)
        if column not in excluded and value is not None:
            clauses.append(f"{table}.{column} = ?")
            parameters.append(str(value))
    if filters.get("from"):
        clauses.append(f"{table}.start_date >= ?")
        parameters.append(filters["from"])
    if filters.get("to"):
        clauses.append(f"{table}.start_date <= ?")
        parameters.append(filters["to"])
    return (" AND ".join(clauses) or "1 = 1"), parameters


def _outcome_columns(filters):
    verb = filters.get("verb")
    if verb is not None and verb not in FAILURE_VERBS:
        raise ValueError(f"Unsupported failure verb: {verb}")
    return (f"pass_{verb}", f"fail_{verb}") if verb else ("pass", "fail")


def query_filtered_outcomes(snapshot_path, filters):
    where, parameters = _where_clause(filters, "daily_stats")
    pass_column, fail_column = _outcome_columns(filters)
    source_where, source_parameters = _where_clause(filters, "source_stats")
    with sqlite3.connect(snapshot_path) as connection:
        passed, failed = connection.execute(
            f"SELECT COALESCE(SUM({pass_column}), 0), "
            f"COALESCE(SUM({fail_column}), 0) FROM daily_stats WHERE {where}",
            parameters,
        ).fetchone()
        matching_files = connection.execute(
            f"SELECT COUNT(DISTINCT source) FROM source_stats "
            f"WHERE {source_where} AND ({pass_column} > 0 OR {fail_column} > 0)",
            source_parameters,
        ).fetchone()[0]
    return int(passed), int(failed), int(matching_files)


def query_grouped_outcomes(snapshot_path, filters, group_columns):
    where, parameters = _where_clause(filters, "daily_stats")
    pass_column, fail_column = _outcome_columns(filters)
    groups = ", ".join(group_columns)
    query = (
        f"SELECT {groups}, COALESCE(SUM({pass_column}), 0), "
        f"COALESCE(SUM({fail_column}), 0) FROM daily_stats "
        f"WHERE {where} GROUP BY {groups}"
    )
    with sqlite3.connect(snapshot_path) as connection:
        rows = connection.execute(query, parameters).fetchall()
    key_size = len(group_columns)
    return {
        tuple(row[:key_size]): {"pass": int(row[-2]), "fail": int(row[-1])}
        for row in rows
    }


def query_aborted_stats(snapshot_path, filters):
    where, parameters = _where_clause(filters, "aborted_stats")
    source_where, source_parameters = _where_clause(filters, "aborted_sources")
    if filters.get("verb") is not None:
        where += " AND aborted_stats.verb = ?"
        source_where += " AND aborted_sources.verb = ?"
        parameters.append(str(filters["verb"]))
        source_parameters.append(str(filters["verb"]))

    with sqlite3.connect(snapshot_path) as connection:
        aborted = connection.execute(
            f"SELECT COALESCE(SUM(aborted), 0) FROM aborted_stats WHERE {where}",
            parameters,
        ).fetchone()[0]
        matching_files = connection.execute(
            f"SELECT COUNT(DISTINCT source) FROM aborted_sources "
            f"WHERE {source_where}",
            source_parameters,
        ).fetchone()[0]
        by_test = dict(connection.execute(
            f"SELECT name, SUM(aborted) FROM aborted_stats WHERE {where} "
            "GROUP BY name ORDER BY name",
            parameters,
        ).fetchall())
        by_system = dict(connection.execute(
            f"SELECT system, SUM(aborted) FROM aborted_stats WHERE {where} "
            "GROUP BY system ORDER BY system",
            parameters,
        ).fetchall())
        target_rows = connection.execute(
            f"SELECT backend, system, SUM(aborted) FROM aborted_stats "
            f"WHERE {where} GROUP BY backend, system ORDER BY backend, system",
            parameters,
        ).fetchall()

    by_target = {}
    for backend, system, count in target_rows:
        by_target.setdefault(backend, {})[system] = int(count)
    return {
        "aborted": int(aborted),
        "matching_files": int(matching_files),
        "by_test": {name: int(count) for name, count in by_test.items()},
        "by_system": {system: int(count) for system, count in by_system.items()},
        "by_target": by_target,
    }
