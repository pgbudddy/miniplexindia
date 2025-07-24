import os
import time
import mysql.connector
from mysql.connector import pooling, Error

DB_RETRIES = int(os.getenv("DB_RETRIES", 30))
DB_RETRY_DELAY = float(os.getenv("DB_RETRY_DELAY", 2))

dbconfig = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", "0909BH0705VV0102HS"),
    "database": os.getenv("MYSQL_DB", "miniplex"),
    "port": int(os.getenv("MYSQL_PORT", 3306)),
}

def _create_pool():
    last_err = None
    for attempt in range(1, DB_RETRIES + 1):
        try:
            print(f"[DB] Trying to connect (attempt {attempt}/{DB_RETRIES}) to {dbconfig['host']}:{dbconfig['port']}")
            return pooling.MySQLConnectionPool(pool_name="mypool", pool_size=10, **dbconfig)
        except Error as e:
            last_err = e
            time.sleep(DB_RETRY_DELAY)
    raise last_err

connection_pool = _create_pool()

def get_db_connection():
    return connection_pool.get_connection()

def close_connection(mydb, mycursor):
    if mydb.is_connected():
        mycursor.close()
        mydb.close()
