import psycopg2
from datetime import datetime
import logging
from core.utils import PhysicalInfo
from typing import Any

# logging
logging.basicConfig(level=logging.INFO)
_logger = logging.getLogger("server.insert_data")

def connect_to_db():
    """Establish a connection to the PostgreSQL database."""
    try:
        connection = psycopg2.connect(
            dbname="bioreactor_db",
            user="postgres",
            password="password",
            host="localhost",
            port="5432"
        )
        return connection
    except Exception as e:
        _logger.error(f"Error connecting to database: {e}")
        return None

def store_data(data: PhysicalInfo, reactor_id: str, experiment_name: str, timestamp: datetime):
    """Insert data into respective PostgreSQL tables based on the sensor."""

    connection = connect_to_db()
    if connection is None:
        return

    cursor = connection.cursor()
    
    try:
        # Always uses only the DATE part (YYYY-MM-DD)
        date_only = timestamp.date()

        # Insert experiment record
        cursor.execute("""
            INSERT INTO experiment (name, date, volume)
            VALUES (%s, %s, %s) RETURNING id
        """, (experiment_name, date_only, None))
        experiment_id = cursor.fetchone()[0]

        # Extract sensor data
        model = data.model.lower()
        channel = data.channels[0]
        value = channel.value
        units = channel.units
        calibration = channel.calibration.file if channel.calibration else None

        insert_map = {
            "visiferm": (
                "INSERT INTO visiferm (experiment_id, date, reactor, value, units) VALUES (%s, %s, %s, %s, %s)",
                (experiment_id, date_only, reactor_id, value, units)
            ),
            "arcph": (
                "INSERT INTO arcph (experiment_id, date, reactor, value, units) VALUES (%s, %s, %s, %s, %s)",
                (experiment_id, date_only, reactor_id, value, units)
            ),
            "analog": (
                "INSERT INTO analog (experiment_id, date, reactor, calibration, value) VALUES (%s, %s, %s, %s, %s)",
                (experiment_id, date_only, reactor_id, calibration, value)
            ),
            "actuator": (
                "INSERT INTO actuator (experiment_id, date, reactor, calibration, value) VALUES (%s, %s, %s, %s, %s)",
                (experiment_id, date_only, reactor_id, calibration, value)
            )
        }

        if model in insert_map:
            query, values = insert_map[model]
            cursor.execute(query, values)
        else:
            _logger.error(f"Unknown model type: {model}")

        connection.commit()
        _logger.info("Data successfully inserted into database.")

    except Exception as e:
        _logger.error(f"Error inserting data: {e}")
    finally:
        cursor.close()
        connection.close()
