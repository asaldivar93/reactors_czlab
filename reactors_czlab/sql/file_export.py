import psycopg2
import csv
from datetime import datetime, timedelta, timezone

def connect():
    return psycopg2.connect(
        dbname="bioreactor_db",
        user="postgres",
        password="password",
        host="localhost",
        port="5432"
    )

def get_date_filter_range(filter_option: str):
    """Return the cutoff date based on filter option"""
    now_utc_date = datetime.now(timezone.utc).date()  # Always use UTC-aware and DATE only

    filter_option = filter_option.strip().lower()  # Normalizing the input

    if filter_option == "24h":
        cutoff_datetime = datetime.now(timezone.utc) - timedelta(hours=24)
        return cutoff_datetime.date()
    elif filter_option == "7d":
        return now_utc_date - timedelta(days=7)
    elif filter_option == "1mo":
        return now_utc_date - timedelta(days=30)
    elif filter_option == "all":
        return None
    else:
        raise ValueError(f"Invalid filter option: {filter_option} (valid: '24h', '7d', '1mo', 'all')")

def export_experiment_data(experiment_name: str, output_csv: str, time_filter: str = "all"):
    conn = connect()
    cur = conn.cursor()

    # Fetch experiment_id from experiment_name
    cur.execute("SELECT id FROM experiment WHERE name = %s", (experiment_name,))
    row = cur.fetchone()

    if not row:
        print(f"No experiment found with name: {experiment_name}")
        cur.close()
        conn.close()
        return

    experiment_id = row[0]

    # Determine date filter
    cutoff_date = get_date_filter_range(time_filter)
    base_conditions = "experiment_id = %s"
    params = [experiment_id]

    if cutoff_date:
        base_conditions += " AND date >= %s"
        params.append(cutoff_date)

    # Queries (with optional date filter)
    queries = {
        "visiferm": f"""
            SELECT 'visiferm' AS source_table, date, reactor, value, units, NULL AS calibration
            FROM visiferm
            WHERE {base_conditions}
        """,
        "arcph": f"""
            SELECT 'arcph' AS source_table, date, reactor, value, units, NULL AS calibration
            FROM arcph
            WHERE {base_conditions}
        """,
        "analog": f"""
            SELECT 'analog' AS source_table, date, reactor, value, NULL AS units, calibration
            FROM analog
            WHERE {base_conditions}
        """,
        "actuator": f"""
            SELECT 'actuator' AS source_table, date, reactor, value, NULL AS units, calibration
            FROM actuator
            WHERE {base_conditions}
        """
    }

    # Combine all rows into one list
    all_rows = []
    for table_name, query in queries.items():
        cur.execute(query, tuple(params))
        rows = cur.fetchall()
        all_rows.extend(rows)

    # Write combined data to CSV
    with open(output_csv, mode='w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(['source_table', 'date', 'reactor', 'value', 'units', 'calibration'])  # Header
        writer.writerows(all_rows)

    print(f"Exported data for experiment '{experiment_name}' with filter '{time_filter}' to {output_csv}'")

    cur.close()
    conn.close()

# Example usage
experiment_name = 'experiment_1'
output_csv = 'experiment_1_filtered_data.csv'

# Options: '24h', '7d', '1mo', 'all'
time_filter = '7d'

export_experiment_data(experiment_name, output_csv, time_filter)
