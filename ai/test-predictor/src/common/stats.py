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
FILTER_COLUMNS = {"name", "backend", "system", "scenario", "attempt"}


def _value(value, default=""):
    try:
        return default if value is None or pd.isna(value) else str(value)
    except (TypeError, ValueError):
        return str(value)


def _date_value(value):
    timestamp = pd.to_datetime(value, errors="coerce", utc=True)
    return None if pd.isna(timestamp) else timestamp.date().isoformat()


def _create_schema(connection):
    connection.executescript("""
        CREATE TABLE processed (
            source TEXT NOT NULL,
            runid TEXT NOT NULL,
            job_id TEXT NOT NULL,
            backend TEXT NOT NULL,
            system TEXT NOT NULL,
            scenario TEXT NOT NULL,
            attempt TEXT NOT NULL,
            name TEXT NOT NULL,
            verb TEXT NOT NULL,
            success INTEGER,
            start_date TEXT
        );
        CREATE TABLE aborted (
            source TEXT NOT NULL,
            backend TEXT NOT NULL,
            system TEXT NOT NULL,
            scenario TEXT NOT NULL,
            attempt TEXT NOT NULL,
            name TEXT NOT NULL,
            verb TEXT NOT NULL,
            start_date TEXT
        );
    """)


def _load_processed(connection, directory):
    insert = """
        INSERT INTO processed VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    for path in glob.glob(os.path.join(directory, "*.ts")):
        frame = pd.read_csv(path)
        rows = []
        for item in frame.to_dict(orient="records"):
            success = pd.to_numeric(item.get("success"), errors="coerce")
            rows.append((
                os.path.basename(path),
                *(_value(item.get(column)) for column in IDENTITY_COLUMNS),
                _value(item.get("verb")),
                None if pd.isna(success) else int(success),
                _date_value(item.get("start")),
            ))
        connection.executemany(insert, rows)


def _load_aborted(connection, directory):
    insert = "INSERT INTO aborted VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    for path in glob.glob(os.path.join(directory, "*.csv")):
        frame = pd.read_csv(path)
        rows = []
        for item in frame.to_dict(orient="records"):
            if _value(item.get("aborted")).strip().lower() != "true":
                continue
            name = _value(item.get("name"))
            variant = _value(item.get("variant"))
            rows.append((
                os.path.basename(path),
                _value(item.get("backend"), "unknown"),
                _value(item.get("system"), "unknown"),
                _value(item.get("scenario")),
                _value(item.get("attempt")),
                f"{name}:{variant}" if variant else name,
                _value(item.get("verb")),
                _date_value(item.get("start")),
            ))
        connection.executemany(insert, rows)


def _create_indexes(connection):
    connection.executescript("""
        CREATE INDEX processed_filters
            ON processed (scenario, backend, system, start_date);
        CREATE INDEX processed_name ON processed (name);
        CREATE INDEX aborted_filters
            ON aborted (scenario, backend, system, start_date);
        CREATE INDEX aborted_name ON aborted (name);
        ANALYZE;
    """)


def build_stats_snapshot(ts_dir, archive_dir, snapshot_path):
    """Build a SQLite stats snapshot and atomically replace the active one."""
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
            _create_schema(connection)
            _load_processed(connection, ts_dir)
            _load_aborted(connection, archive_dir)
            _create_indexes(connection)
            connection.commit()
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
    return os.path.isfile(snapshot_path)


def _where_clause(filters, table="processed", excluded=()):
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


def _outcomes_query(filters, group_columns=()):
    where, parameters = _where_clause(filters)
    failure_verbs = (filters["verb"],) if filters.get("verb") else FAILURE_VERBS
    verb_placeholders = ", ".join("?" for _ in failure_verbs)
    identity = ", ".join((*group_columns, *IDENTITY_COLUMNS))
    group_select = ", ".join(group_columns)
    outer_group = f"GROUP BY {group_select}" if group_columns else ""
    outer_select = f"{group_select}," if group_columns else ""
    query = f"""
        WITH outcomes AS (
            SELECT {identity},
                   MAX(CASE WHEN verb IN ({verb_placeholders}) AND success = 0
                            THEN 1 ELSE 0 END) AS failed,
                   MAX(CASE WHEN verb = 'executing' AND success = 1
                            THEN 1 ELSE 0 END) AS passed
            FROM processed
            WHERE {where}
            GROUP BY {identity}
        )
        SELECT {outer_select}
               COALESCE(SUM(CASE WHEN passed = 1 AND failed = 0 THEN 1 ELSE 0 END), 0),
               COALESCE(SUM(failed), 0)
        FROM outcomes
        {outer_group}
    """
    return query, [*failure_verbs, *parameters]


def query_filtered_outcomes(snapshot_path, filters):
    query, parameters = _outcomes_query(filters)
    where, file_parameters = _where_clause(filters)
    failure_verbs = (filters["verb"],) if filters.get("verb") else FAILURE_VERBS
    placeholders = ", ".join("?" for _ in failure_verbs)
    file_query = f"""
        SELECT COUNT(DISTINCT source)
        FROM processed
        WHERE {where}
          AND ((verb = 'executing' AND success = 1)
               OR (verb IN ({placeholders}) AND success = 0))
    """
    with sqlite3.connect(snapshot_path) as connection:
        passed, failed = connection.execute(query, parameters).fetchone()
        matching_files = connection.execute(
            file_query, [*file_parameters, *failure_verbs]
        ).fetchone()[0]
    return int(passed), int(failed), int(matching_files)


def query_grouped_outcomes(snapshot_path, filters, group_columns):
    query, parameters = _outcomes_query(filters, group_columns)
    with sqlite3.connect(snapshot_path) as connection:
        rows = connection.execute(query, parameters).fetchall()
    key_size = len(group_columns)
    return {
        tuple(row[:key_size]): {"pass": int(row[-2]), "fail": int(row[-1])}
        for row in rows
    }


def query_aborted_stats(snapshot_path, filters):
    where, parameters = _where_clause(filters, table="aborted")
    if filters.get("verb") is not None:
        where += " AND aborted.verb = ?"
        parameters.append(str(filters["verb"]))
    query = f"SELECT source, backend, system, name FROM aborted WHERE {where}"
    with sqlite3.connect(snapshot_path) as connection:
        rows = connection.execute(query, parameters).fetchall()

    by_test = Counter(row[3] for row in rows)
    by_system = Counter(row[2] for row in rows)
    by_target = {}
    for _, backend, system, _ in rows:
        by_target.setdefault(backend, Counter())[system] += 1
    return {
        "aborted": len(rows),
        "matching_files": len({row[0] for row in rows}),
        "by_test": dict(sorted(by_test.items())),
        "by_system": dict(sorted(by_system.items())),
        "by_target": {
            backend: dict(sorted(systems.items()))
            for backend, systems in sorted(by_target.items())
        },
    }