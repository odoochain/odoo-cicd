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
class Duplicate(Exception): pass

while True:
    try:
        while True:
            uid = login(username, pwd)
            os.system("./cicd kill odoo_queuejobs")
            jobs = exe('queue.job', 'search', [])
            exe('queue.job', 'unlink', jobs)
            os.system("./cicd up -d odoo_queuejobs")
            timeout = arrow.get().shift(seconds=60)
            count_runs += 1
            if exe("queue.job", "search_count", []) > 0:
                raise Exception("No jobs expected")

            testrun_id = exe('cicd.test.run', 'create', {
                'branch_id': 3,
                'commit_id': 825,
            })
            print(f"Testrun id: {testrun_id}")

            last_print = None
            while True:
                exe('ir.cron', [49], 'method_direct_trigger')
                lines = exe("cicd.test.run", "read", [testrun_id], ['line_ids'])[0]['line_ids']
                lines = exe("cicd.test.run.line", "read", lines, ['name'])
                line_names = list(map(lambda x: x['name'], lines))
                txt = (
                    f"Count Error: {count_error}, len: {len(lines)}"
                    f", Count Runs: {count_runs}"
                )
                if txt != last_print:
                    print(txt)
                    last_print = txt
                time.sleep(0.1)

                try:
                    for item in list(set(line_names)):
                        if line_names.count(item) > 1:
                            count_error += 1
                            raise Duplicate(item)
                except Duplicate as ex:
                    print(f"Duplicate: {ex}")
                    break

                search_result = exe("queue.job", "search", [])
                if search_result:
                    search_result = exe("queue.job", "read", search_result, ['state'])
                    states = list(map(lambda x: x['state'], search_result))
                    if all(x in ['done', 'failed'] for x in states):
                        print("All jobs done")
                        break

                if arrow.get() > timeout:
                    print("Timeout")
                    break

    except Exception as ex:
        print(ex)
        time.sleep(1)

