import inspect
import arrow
import os
import xmlrpc.client
import time
import subprocess
import sys

host = "http://localhost:9991"
username = "admin"
pwd = "1"
db = "cicdadmin"

def login(username, password):
    socket_obj = xmlrpc.client.ServerProxy('%s/xmlrpc/common' % (host))
    uid = socket_obj.login(db, username, password)
    return uid

def exe(*params):
    global uid
    socket_obj = xmlrpc.client.ServerProxy('%s/xmlrpc/object' % (host))
    res = None
    try:
        res = socket_obj.execute(db, uid, pwd, *params)
    except xmlrpc.client.Fault as ex:
        res = None
    finally:
        return res

count_error = 0
count_runs = 0

while True:
    try:
        while True:
            uid = login(username, pwd)
            time.sleep(20)
            exe("cicd.test.run", "rerun", [173])
            time.sleep(3)
            timeout = arrow.get().shift(seconds=20)
            count_runs += 1

            while arrow.get() < timeout:
                lines = exe("cicd.test.run", "read", [173], ['line_ids'])[0]['line_ids']
                exe('ir.cron', [49], 'method_direct_trigger')
                print((
                    f"Count Error: {count_error}, len: {len(lines)}"
                    f", Count Runs: {count_runs}"
                ))
                time.sleep(0.3)

                if len(lines) > 12:
                    count_error += 1
                    break

    except Exception as ex:
        print(ex)
        time.sleep(1)

