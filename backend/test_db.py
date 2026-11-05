
from app.database import get_connection


def main():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), NOW()")
            database, server_time = cur.fetchone()

    print("Database connected successfully!")
    print("Database:", database)
    print("Server time:", server_time)


if __name__ == "__main__":
    main()
