import psycopg2
import csv
from datetime import datetime

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
        print(f"Error connecting to database: {e}")
        return None

def export_table_to_csv(connection, table_name, output_filename):
    """Fetch data from a table and write it to a CSV file."""
    try:
        cursor = connection.cursor()

        # Fetch all rows from the table
        cursor.execute(f"SELECT * FROM {table_name}")
        rows = cursor.fetchall()

        # Write data to a CSV file
        with open(output_filename, mode='w', newline='') as file:
            writer = csv.writer(file)
            
            # Write header (column names)
            colnames = [desc[0] for desc in cursor.description]
            writer.writerow(colnames)
            
            # Write the data rows
            for row in rows:
                writer.writerow(row)

        print(f"Data from table '{table_name}' has been exported to {output_filename}")

    except Exception as e:
        print(f"Error exporting data from table '{table_name}': {e}")
    finally:
        cursor.close()

def export_all_tables_to_csv():
    """Export data from all tables to CSV files."""
    connection = connect_to_db()
    
    if connection is None:
        return  # If connection fails, stop further processing

    try:
        # Export data from each table to CSV files
        tables = ["visiferm", "arcph", "analog", "actuator"]

        for table in tables:
            output_filename = f"{table}_{datetime.now().strftime('%Y%m%d%H%M%S')}.csv"
            export_table_to_csv(connection, table, output_filename)

    finally:
        # Close the connection
        connection.close()

if __name__ == "__main__":
    export_all_tables_to_csv()
