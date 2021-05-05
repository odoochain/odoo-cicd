#!/usr/bin/env python3
import psycopg2
from dotenv import load_dotenv
import os
load_dotenv()  

conn = psycopg2.connect(host=os.environ['DB_HOST'], port=int(os.environ['DB_PORT']), user=os.environ['DB_USER'], password=os.environ['DB_PASSWORD'], database='postgres')
cr = conn.cursor()
try:
    cr.execute("select datname from pg_database;")
    dbnames = [x[0] for x in cr.fetchall()]
    for dbname in dbnames:
        if dbname == 'rsodoo_r2o_2461':
            cr.execute(f"alter database {dbname} rename to r2o_2461a")
            continue
        if 'rsodoo_' in dbname:
            cr.execute(f"alter database {dbname} rename to {dbname.replace('rsodoo_', '')}")
    conn.commit()

finally:
    cr.close()
    conn.close()