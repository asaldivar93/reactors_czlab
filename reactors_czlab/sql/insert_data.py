import psycopg2
from datetime import datetime
import logging
import csv
from core.utils import PhysicalInfo, Channel, Calibration
from typing import Any

# Set up logging for better visibility
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
    """Insert data into respective PostgreSQL tables based on the model."""
    
    connection = connect_to_db()
    
    if connection is None:
        return  # If the connection fails, stop further processing

    # Create a cursor to interact with the database
    cursor = connection.cursor()
    
    try:
        # Directly insert the experiment data without checking if it already exists
        cursor.execute("""
            INSERT INTO experiment (name, date, volume)
            VALUES (%s, %s, %s) RETURNING id
        """, (experiment_name, timestamp.date(), None))
        
        # Fetch the generated experiment_id
        experiment_id = cursor.fetchone()[0]
        
        # Insert data for different models (visiferm, arcph, analog, actuator)
        model = data.model.lower()  # Convert model to lowercase to match table names
        
        # Extracting values from the data
        channel = data.channels[0]
        value = channel.value
        units = channel.units
        calibration = channel.calibration.file if channel.calibration else None
        
        # Defining insert queries for different models
        insert_map = {
            "visiferm": (
                "INSERT INTO visiferm (experiment_id, date, reactor, value, units) VALUES (%s, %s, %s, %s, %s)",
                (experiment_id, timestamp, reactor_id, value, units)
            ),
            "arcph": (
                "INSERT INTO arcph (experiment_id, date, reactor, value, units) VALUES (%s, %s, %s, %s, %s)",
                (experiment_id, timestamp, reactor_id, value, units)
            ),
            "analog": (
                "INSERT INTO analog (experiment_id, date, reactor, calibration, value) VALUES (%s, %s, %s, %s, %s)",
                (experiment_id, timestamp, reactor_id, calibration, value)
            ),
            "actuator": (
                "INSERT INTO actuator (experiment_id, date, reactor, calibration, value) VALUES (%s, %s, %s, %s, %s)",
                (experiment_id, timestamp, reactor_id, calibration, value)
            )
        }
        
        # Check if the model is in our map and execute the corresponding query
        if model in insert_map:
            query, values = insert_map[model]
            cursor.execute(query, values)
        else:
            _logger.error(f"Unknown model type: {model}")
        
        # Commit the changes to the database
        connection.commit()
        _logger.info("Data successfully inserted into database.")

    except Exception as e:
        _logger.error(f"Error inserting data: {e}")
    finally:
        # Close the cursor and connection
        cursor.close()
        connection.close()
        

