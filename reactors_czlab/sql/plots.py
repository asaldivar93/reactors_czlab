import psycopg2
import polars as pl
import logging
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from datetime import datetime
import matplotlib.dates as mdates

DB_CONFIG = {
    "dbname": "bioreactor_db",
    "user": "postgres",
    "password": "password",
    "host": "localhost",
    "port": "5432"
}

# logging
logging.basicConfig(level=logging.INFO)
_logger = logging.getLogger("live_plot")

# DB Connection
def connect_to_db():
    """Connect to the PostgreSQL database."""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        _logger.info("Database connection established.")
        return conn
    except Exception as e:
        _logger.error(f"Database connection failed: {e}")
        return None

# Fetching the data 
def fetch_latest_experiment(cursor):
    """Fetch the latest experiment's id and name."""
    cursor.execute("SELECT id, name FROM experiment ORDER BY id DESC LIMIT 1")
    result = cursor.fetchone()
    if result:
        experiment_id, experiment_name = result
        return experiment_id, experiment_name
    else:
        raise ValueError("No experiments found in the database.")

def fetch_dataframe(cursor, query: str, params: tuple) -> pl.DataFrame:
    """Run a SQL query and return a Polars DataFrame."""
    cursor.execute(query, params)
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()
    if not rows:
        _logger.warning("Query returned no data.")
        return pl.DataFrame(schema={col: pl.Null for col in columns})  # Empty dataframe
    return pl.DataFrame(rows, schema=columns)

def get_latest_experiment_and_sensor_data():
    """Fetch and return Polars DataFrames"""
    connection = connect_to_db()
    if connection is None:
        raise ConnectionError("Failed to connect to database.")

    cursor = connection.cursor()

    try:
        experiment_id, experiment_name = fetch_latest_experiment(cursor)
        _logger.info(f"Using latest experiment: ID = {experiment_id}, Name = '{experiment_name}'")

        experiment_query = "SELECT * FROM experiment WHERE id = %s"
        experiment_df = fetch_dataframe(cursor, experiment_query, (experiment_id,))
        _logger.info(f"Fetched experiment_df with {len(experiment_df)} rows")

        sensor_tables = ['visiferm', 'arcph', 'analog', 'actuator']
        sensor_dfs = {}

        for table in sensor_tables:
            query = f"SELECT * FROM {table} WHERE experiment_id = %s"
            df = fetch_dataframe(cursor, query, (experiment_id,))
            sensor_dfs[table] = df
            _logger.info(f"Fetched {table}_df with {len(df)} rows")

        return experiment_name, experiment_df, sensor_dfs

    finally:
        cursor.close()
        connection.close()
        _logger.info("Database connection closed.")

# Live plotting using matplotlib
def setup_plots():
    fig, axs = plt.subplots(2, 2, figsize=(12, 8))
    fig.suptitle('Live Sensor Data (Latest Experiment)', fontsize=16)
    axs = axs.flatten()

    titles = ['Visiferm', 'Arcph', 'Analog', 'Actuator']
    plots = {}

    for ax, title in zip(axs, titles):
        ax.set_title(title)
        ax.set_xlabel('Date')
        ax.set_ylabel('Value')
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))  # Date only
        line, = ax.plot([], [], label=title, marker='o')
        ax.legend(loc='upper left')
        plots[title.lower()] = (ax, line)

    plt.tight_layout(rect=[0, 0, 1, 0.95])
    return fig, plots

def update(frame, plots):
    try:
        experiment_name, experiment_df, sensor_dfs = get_latest_experiment_and_sensor_data()
        _logger.info(f"Updating plots for experiment '{experiment_name}'")

        for sensor_name, (ax, line) in plots.items():
            df = sensor_dfs[sensor_name]
            if df.is_empty():
                line.set_data([], [])
                continue

            # Ensuring date column is sorted
            df = df.sort("date")
            dates = df["date"].to_numpy()
            values = df["value"].to_numpy()

            # Converting Polars Date column (YYYY-MM-DD) to matplotlib date numbers
            dates = [datetime.strptime(str(d), "%Y-%m-%d") for d in dates]

            # Update plot
            line.set_data(dates, values)
            ax.relim()
            ax.autoscale_view()

    except Exception as e:
        _logger.error(f"Error during update: {e}")

if __name__ == "__main__":
    fig, plots = setup_plots()

    ani = FuncAnimation(fig, update, fargs=(plots,), interval=2000) 
    plt.show()
