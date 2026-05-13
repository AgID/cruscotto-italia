"""DuckDB helper for ETL transformations.

DuckDB is the workhorse: it reads CSV/JSON/Parquet directly from disk
or HTTP, applies SQL transformations, and writes Parquet output.

Usage:
    with duck_session() as con:
        con.execute("CREATE TABLE x AS SELECT * FROM read_csv_auto('input.csv')")
        con.execute("COPY x TO 'output.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)")
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb


@contextmanager
def duck_session(memory_limit: str = "2GB") -> Iterator[duckdb.DuckDBPyConnection]:
    """Context manager for a DuckDB in-memory session.

    Args:
        memory_limit: max memory (DuckDB will spill to disk if needed).
    """
    con = duckdb.connect(":memory:")
    con.execute(f"SET memory_limit='{memory_limit}'")
    con.execute("INSTALL httpfs; LOAD httpfs;")
    try:
        yield con
    finally:
        con.close()


def write_parquet(
    con: duckdb.DuckDBPyConnection,
    sql: str,
    output_path: Path | str,
    compression: str = "zstd",
) -> dict:
    """Execute SQL and write result to Parquet.

    Returns a dict with row_count and output_path.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    con.execute(
        f"COPY ({sql}) TO '{output_path}' "
        f"(FORMAT PARQUET, COMPRESSION '{compression.upper()}')"
    )
    # Count rows written
    row_count = con.execute(f"SELECT COUNT(*) FROM ({sql})").fetchone()[0]
    return {"row_count": row_count, "output_path": str(output_path)}
