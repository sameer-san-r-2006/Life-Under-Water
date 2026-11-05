from pathlib import Path
import re

from app.database import get_connection


MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[2]
    / "database"
    / "migrations"
)

REQUIRED_SENSOR_READING_COLUMNS = {
    "id",
    "device_id",
    "recorded_at",
    "temperature_c",
    "temperature_valid",
    "tds_raw_adc",
    "tds_voltage_v",
    "tds_ppm",
    "tds_legacy_demo_estimate_ppm",
    "tds_valid",
    "tds_calibration_status",
    "tds_conversion_method",
    "tds_source",
    "turbidity_raw_adc",
    "turbidity_voltage_v",
    "turbidity_ntu",
    "turbidity_valid",
    "turbidity_calibration_status",
    "cloudiness_index",
    "cloudiness_valid",
}


def verify_sensor_readings_schema(cursor) -> None:
    cursor.execute(
        """
        SELECT attname
        FROM pg_attribute
        WHERE attrelid = to_regclass(%s)
          AND attnum > 0
          AND NOT attisdropped
        """,
        ("sensor_readings",),
    )
    existing_columns = {row[0] for row in cursor.fetchall()}
    missing_columns = REQUIRED_SENSOR_READING_COLUMNS - existing_columns
    if missing_columns:
        raise RuntimeError(
            "sensor_readings is missing required columns after migrations: "
            + ", ".join(sorted(missing_columns))
        )


def has_sql_content(statement: str) -> bool:
    return any(
        line.strip() and not line.lstrip().startswith("--")
        for line in statement.splitlines()
    )


def split_sql_statements(sql: str) -> list[str]:
    """Split SQL on statement delimiters, not semicolons inside literals/comments."""
    statements = []
    start = 0
    index = 0
    state = "normal"
    block_depth = 0
    dollar_quote = None

    while index < len(sql):
        char = sql[index]
        next_char = sql[index + 1] if index + 1 < len(sql) else ""

        if state == "normal":
            if char == "-" and next_char == "-":
                state = "line_comment"
                index += 2
                continue
            if char == "/" and next_char == "*":
                state = "block_comment"
                block_depth = 1
                index += 2
                continue
            if char == "'":
                state = "single_quote"
                index += 1
                continue
            if char == '"':
                state = "double_quote"
                index += 1
                continue
            if char == "$":
                match = re.match(r"\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$", sql[index:])
                if match:
                    dollar_quote = match.group(0)
                    state = "dollar_quote"
                    index += len(dollar_quote)
                    continue
            if char == ";":
                statement = sql[start:index].strip()
                if has_sql_content(statement):
                    statements.append(statement)
                start = index + 1
        elif state == "single_quote":
            if char == "'" and next_char == "'":
                index += 2
                continue
            if char == "'":
                state = "normal"
        elif state == "double_quote":
            if char == '"' and next_char == '"':
                index += 2
                continue
            if char == '"':
                state = "normal"
        elif state == "line_comment":
            if char in "\r\n":
                state = "normal"
        elif state == "block_comment":
            if char == "/" and next_char == "*":
                block_depth += 1
                index += 2
                continue
            if char == "*" and next_char == "/":
                block_depth -= 1
                index += 2
                if block_depth == 0:
                    state = "normal"
                continue
        elif state == "dollar_quote" and sql.startswith(dollar_quote, index):
            index += len(dollar_quote)
            state = "normal"
            dollar_quote = None
            continue

        index += 1

    statement = sql[start:].strip()
    if has_sql_content(statement):
        statements.append(statement)
    return statements


def main() -> None:
    migrations = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not migrations:
        raise RuntimeError(f"No SQL migrations found in {MIGRATIONS_DIR}")

    with get_connection() as connection:
        with connection.cursor() as cursor:
            for migration_path in migrations:
                migration_sql = migration_path.read_text(encoding="utf-8")
                for statement in split_sql_statements(migration_sql):
                    cursor.execute(statement)
                print(f"Applied {migration_path.name}.")
            verify_sensor_readings_schema(cursor)
            print("Verified sensor_readings columns used by the API and serial writer.")


if __name__ == "__main__":
    main()
