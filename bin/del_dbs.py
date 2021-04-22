#!/usr/bin/env python3

import subprocess
import time
sql = ["docker-compose", "exec", "-u", "postgres", "cicd_postgres", "psql", "-P", "pager=off", "-U", "cicd", "-c"]
dbs = subprocess.check_output(sql + ["SELECT datname FROM pg_database;"]).decode('utf-8').split('\n')

#dbs = [x.strip() for x in dbs if 'live' in x]
print(dbs)


for db in dbs:
    if '2461' in db: continue
    if 'template' in db: continue
    if 'cicd' in db: continue
    if 'postgres' in db: continue
    if '----' in db: continue
    if 'datname' in db: continue
    if 'restoring' in db: continue
    print(db)
    subprocess.check_call(sql + [f"drop database {db};"])
