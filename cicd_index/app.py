#TODO clean source not only in workspace
#TODO label in containers per project
import base64
import shutil
import os
import time
from bson import ObjectId
from flask import redirect
from operator import itemgetter
import docker as Docker
import arrow
import humanize
import subprocess
from flask import jsonify
from flask import make_response
from flask import Flask
from flask import render_template
from flask import url_for
from datetime import datetime
from flask import request
from collections import defaultdict
import pymongo
import json
from pathlib import Path
from bson.json_util import dumps
import threading
import logging
# import jenkins
import urllib
import psycopg2



from pymongo import MongoClient
mongoclient = MongoClient(
    os.environ["MONGO_HOST"],
    int(os.environ['MONGO_PORT']),
    username=os.environ['MONGO_USERNAME'],
    password=os.environ['MONGO_PASSWORD'],
    connectTimeoutMS=20000, socketTimeoutMS=20000, serverSelectionTimeoutMS=20000,
)
db = mongoclient.get_database('cicd_sites')

FORMAT = '[%(levelname)s] %(name) -12s %(asctime)s %(message)s'
logging.basicConfig(format=FORMAT)
logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger('')  # root handler

BOOL_VALUES = ['1', 1, 'true', 'True', 'y']
app = Flask(
    __name__,
    static_folder='/_static_index_files',
)

docker = Docker.from_env()


def cycle_down_apps():
    while True:
        try:
            sites = db.sites.find({'name': 1, 'last_access': 1})
            for site in sites:
                logger.debug(f"Checking site to cycle down: {site['name']}")
                if (arrow.get() - arrow.get(site.get('last_access', '1980-04-04') or '1980-04-04')).total_seconds() > 2 * 3600: # TODO configurable
                    if _get_docker_state(site['name']) == 'running':
                        logger.info(f"Cycling down instance due to inactivity: {site['name']}")
                        _stop_instance(site['name'])

        except Exception as e:
            logging.error(e)
        time.sleep(10)


t = threading.Thread(target=cycle_down_apps)
t.daemon = True
t.start()


class JSONEncoder(json.JSONEncoder):
    # for encoding ObjectId
    def default(self, o):
        if isinstance(o, ObjectId):
            return str(o)

        return super(JSONEncoder, self).default(o)


app.json_encoder = JSONEncoder

@app.route("/last_access")
def last_access():
    if not request.args.get('site'):
        raise Exception('site missing')
    site = db.sites.find_one({'name': request.args.get('site')})
    if site:
        db.sites.update_one({
            '_id': site['_id'],
        }, {'$set': {
            'last_access': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        }, upsert=False)
    return jsonify({'result': 'ok'})

@app.route("/sites")
def show_sites():
    return jsonify(list(db.sites.find()))

@app.route("/next_instance")
def next_instance_name():
    branch = request.args.get('branch')
    key = request.args.get('key')
    assert branch
    assert key
    sites = list(db.sites.find({
        'git_branch': branch,
        'key': key
    }))
    sites = sorted(sites, key=lambda x: x['index'])
    index = max(list(filter(bool, [x.get('index') for x in sites])) + [0])

    info = {
        'commit_before': '',
    }
    if index:
        site = [x for x in sites if x['index'] == index]
        info['commit_before'] = site[0]['git_sha']
    info['index'] = 1 if 'kept' else index + 1
    info['name'] = f"{branch}_{key}_{str(info['index']).zfill(3)}"
    return jsonify(info)

@app.route('/register', methods=['POST'])
def register_site():
    if request.method == 'POST':
        site = dict(request.json)
        sites = list(db.sites.find({
            "git_branch": site['git_branch'],
        }))
        result = {'result': 'ok'}

        branch = db.branches.find_one({
            "git_branch": site['git_branch'],
        })
        if not branch:
            db.branches.insert({
                'git_branch': site['git_branch'],
            })
        else:
            for key in ['description', 'author']:
                update = {}
                if site.get(key):
                    update[key] = site[key]
                if update:
                    db.branches.update_one({'_id': branch['_id']}, {'$set': update}, upsert=False)

        if not sites:
            db.sites.insert_one(site)
            result['existing'] = True

        return jsonify(result)

    raise Exception("only POST")

@app.route("/site", methods=["GET"])
def site():
    q = {}
    for key in [
        'branch', 'name',
    ]:
        if request.args.get(key):
            q[key] = request.args.get(key)
    site = db.sites.find(q)
    return jsonify(site)

@app.route("/instance/start")
def start_instance(name=None):
    name = name or request.args['name']
    containers = docker.containers.list(all=True, filters={'name': [name]})
    for container in containers:
        container.start()
    return jsonify({
        'result': 'ok',
    })

def _stop_instance(name):
    containers = docker.containers.list(all=False, filters={'name': [name]})
    for container in containers:
        container.stop()

@app.route("/instance/stop")
def stop_instance(name=None):
    name = name or request.args['name']
    _stop_instance(name)
    return jsonify({
        'result': 'ok'
    })

@app.route("/instance/status")
def instance_state():
    name = request.args['name']
    return jsonify({
        'state': 'running' if _get_docker_state(name) else 'stopped'
    })

@app.route("/set_updating")
def set_updating():
    name = request.args['name']

    site = db.sites.find_one({'name': name})
    if not site:
        raise Exception(f"site not found: {name}")
    db.sites.update_one({'_id': site['_id']}, {'$set': {
        'updating': request.args['value'] in BOOL_VALUES,
    }}, upsert=False)
    return jsonify({
        'result': 'ok',
    })

@app.route("/notify_instance_updating")
def notify_instance_updating():

    return jsonify({
        'result': 'ok',
    })


@app.route("/notify_instance_updated")
def notify_instance_updated():
    info = {
        'name': request.args['name'],
        'sha': request.args['sha'],
    }
    assert info['name']
    assert info['sha']
    for extra_args in [
        'update_time',
        'dump_date',
        'dump_name',
    ]:
        info[extra_args] = request.args.get(extra_args)

    info['date'] = arrow.get().strftime("%Y-%m-%d %H:%M:%S")

    db.updates.insert_one(info)

    site = db.sites.find_one({'name': info['name']})
    if not site:
        raise Exception(f"site not found: {info['name']}")
    db.sites.update_one({'_id': site['_id']}, {'$set': {
        'duration': request.args.get('duration'),
        'updated': datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
    }}, upsert=False)

    # if there is dump information, then store at site
    if request.args.get('dump_name'):
        db.sites.update_one({'_id': site['_id']}, {'$set': {
            'dump_name': request.args['dump_name'],
            'dump_date': request.args['dump_date'],
        }}, upsert=False)

    return jsonify({
        'result': 'ok'
    })

@app.route("/last_successful_sha")
def last_success_full_sha():
    info = {
        'name': request.args['name'],
    }
    assert info['name']

    updates = list(db.updates.find(info).sort([("date", pymongo.DESCENDING)]).limit(1))
    if updates:
        return jsonify({
            'sha': updates[0]['sha']
        })
    return jsonify({
        'sha': '',
    })

def _get_jenkins(crumb=True):
    # res = jenkins.Jenkins('http://192.168.101.122:8080', username='admin', password='1')
    from jenkinsapi.utils.crumb_requester import CrumbRequester
    from jenkinsapi.jenkins import Jenkins
    crumb_requester = CrumbRequester(
        username=os.environ['JENKINS_USER'],
        password=os.environ["JENKINS_PASSWORD"],
        baseurl=os.environ["JENKINS_URL"],
    )

    res = Jenkins(
        os.environ["JENKINS_URL"],
        username=os.environ["JENKINS_USER"],
        password=os.environ["JENKINS_PASSWORD"],
        requester=crumb_requester if crumb else None # https://stackoverflow.com/questions/45199374/remotely-build-specific-jenkins-branch/45200202
    )
    # print(f"Jenkins {res.get_whoami()} and version {res.get_version()}")
    return res

def _get_jenkins_job(branch):
    jenkins = _get_jenkins()
    job = jenkins[f"{os.environ['JENKINS_JOB_MULTIBRANCH']}/{branch}"]
    return job

def _reset_instance_in_db(name):
    info = {
        'name': request.args['name'],
    }
    db.sites.remove(info)
    db.updates.remove(info)

def _set_marker_and_restart(name, settings):
    site = db.sites.find_one({'name': name})
    db.sites.update_one(
        {'_id': site['_id']},
        {'$set': settings},
        upsert=False
    )

    jenkins = _get_jenkins()
    job = jenkins[f"{os.environ['JENKINS_JOB_MULTIBRANCH']}/{site['git_branch']}"]
    job.invoke()
    return jsonify({
        'result': 'ok',
    })

@app.route('/trigger/rebuild')
def trigger_rebuild():
    site = db.sites.find_one({'name': request.args['name']})
    _set_marker_and_restart(
        request.args['name'],
        {
            'reset-db-at-next-build': True
        }
    )
    db.updates.remove({'name': site['name']})
    return jsonify({
        'result': 'ok',
    })

def _get_docker_state(name):
    docker.ping()
    containers = docker.containers.list(all=True, filters={'name': [name]})
    states = set(map(lambda x: x.status, containers))
    return 'running' in states

def format_date(dt):
    DATE_FORMAT = os.environ['DATE_FORMAT'].replace("_", "%")
    tz = os.environ['DISPLAY_TIMEZONE']
    arrow.get(dt)
    return arrow.get(dt).to(tz).strftime(DATE_FORMAT)

def _format_dates_in_records(records):
    for rec in records:
        for k in rec:
            if not isinstance(rec[k], str):
                continue
            try:
                rec[k] = format_date(arrow.get(rec[k]))
            except Exception:
                continue
    return records

@app.route("/possible_dumps")
def possible_dumps():
    path = Path("/opt/dumps")
    dump_names = sorted([x.name for x in path.glob("*")])

    def _get_value(filename):
        date = arrow.get((path / filename).stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        return f"{filename} [{date}]"

    dump_names = [{'id': x, 'value': _get_value(x)} for x in dump_names]
    return jsonify(dump_names)

@app.route("/data/site/live_values")
def site_jenkins():
    sites = list(db.sites.find())
    for site in sites:
        try:
            job = _get_jenkins_job(site['git_branch'])
        except Exception as ex:
            site['last_build'] = f"Error: {ex}"
        else:
            if job:
                last_build = job.get_last_build_or_none()
                if last_build:
                    site['last_build'] = last_build.get_status()
                    site['duration'] = round(last_build.get_duration().total_seconds(), 0)
                site['update_in_progress'] = job.is_running()
            site['docker_state'] = 'running' if _get_docker_state(site['name']) else 'stopped'
    return jsonify(sites)


@app.route("/data/sites", methods=["GET", "POST"])
def data_variants():
    _filter = {}
    if request.args.get('git_branch', None):
        _filter['git_branch'] = request.args['git_branch']
    if request.args.get('name', None):
        _filter['name'] = request.args['name']

    sites = list(db.sites.find(_filter))

    sites = _format_dates_in_records(sites)
    sites = sorted(sites, key=lambda x: x.get('name'))

    for site in sites:
        site['id'] = site['_id']
        site['update_in_progress'] = False
        site['repo_url'] = f"{os.environ['REPO_URL']}/-/commit/{site['git_sha']}"

    return jsonify(sites)

@app.route('/')
def index_func():

    return render_template(
        'index.html',
        DATE_FORMAT=os.environ['DATE_FORMAT'].replace("_", "%"),
    )

def _validate_input(data, int_fields=[]):
    data = dict(data)
    for int_field in int_fields:
        if int_field in data:
            try:
                data[int_field] = int(data[int_field].strip())
            except ValueError as ex:
                print(ex)
                data.pop(int_field)
    for k, v in list(data.items()):
        if v in ['true', 'True']:
            data[k] = True
        elif v in ['false', 'False']:
            data[k] = False
        elif v == 'undefined':
            data[k] = None

    if '_id' in data and isinstance(data['_id'], str):
        data['_id'] = ObjectId(data['_id'])
    return data


@app.route('/update/site', methods=["GET", "POST"])
def update_site():
    if request.method == 'POST':
        data = request.form
    else:
        data = request.args
    data = _validate_input(data, int_fields=[])
    if '_id' not in data and 'git_branch' in data:
        branch_name = data.pop('git_branch')
        site = db.sites.find_one({'git_branch': branch_name})
        id = site['_id']
    else:
        id = ObjectId(data.pop('_id'))
    db.sites.update_one(
        {'_id': id},
        {'$set': data},
        upsert=False
    )
    return jsonify({'result': 'ok'})

@app.route('/start')
def start_cicd():
    return _start_cicd()

def _start_cicd():
    # name = request.cookies['delegator-path']
    name = request.args['name']
    if not _get_docker_state(name):
        start_instance(name=name)
        for i in range(30):
            if _get_docker_state(name):
                break
            time.sleep(1)
        else:
            url = request.url.split(request.host)[0] + request.host # TODO messy
            return redirect(url + "/index?" + urllib.parse.urlencode({
                "message": f"Please try again. Instance {name} not started within timeout.",
            }, quote_via=urllib.parse.quote_plus))

    response = make_response(
        render_template(
            'start_cicd.html',
            initial_path=request.args.get('initial_path') or '/web/login'
        ),
    )
    response.set_cookie('delegator-path', name)
    return response

def get_setting(key, default=None):
    config = db.config.find_one({'key': key})
    if not config:
        return default
    return config['value']


def store_setting(key, value):
    db.sites.update_one({
        'key': key,
    }, {'$set': {
        'value': value,
    }
    }, upsert=True)

def _get_shell_url(command):
    pwd = base64.encodestring('odoo'.encode('utf-8')).decode('utf-8')
    shellurl = f"/console/?hostname=127.0.0.1&username=root&password={pwd}&command="
    shellurl += ' '.join(command)
    return shellurl

@app.route("/show_logs")
def show_logs():
    name = request.args.get('name')
    name += '_odoo'
    containers = docker.containers.list(all=True, filters={'name': [name]})
    containers = [x for x in containers if x.name == name]
    shell_url = _get_shell_url(["docker", "logs", "-f", containers[0].id])
    return redirect(shell_url)

@app.route("/debug_instance")
def debug_instance():
    name = request.args.get('name')
    site_name = name
    name += '_odoo'
    # kill existing container and start odoo with debug command
    containers = docker.containers.list(all=True, filters={'name': [name]})
    containers = [x for x in containers if x.name == name]
    for container in containers:
        container.stop()
    shell_url = _get_shell_url([
        "cd", f"/{os.environ['WEBSSH_CICD_WORKSPACE']}/cicd_instance_{site_name}", ";",
        "/usr/bin/python3",  "/opt/odoo/odoo", "-f", "--project-name", site_name, "debug", "odoo", "--command", "/odoolib/debug.py",
    ])
    # TODO make safe; no harm on system, probably with ssh authorized_keys

    return redirect(shell_url)

@app.route("/restart_docker")
def restart_docker():
    site_name = request.args.get('name')
    if site_name != 'all':
        site_name = [site_name]
    else:
        site_name = [x['name'] for x in db.sites.find({})]

    containers_all = []
    for site_name in site_name:
        containers = [x for x in docker.containers.list(all=True) if site_name in x.name]
        containers_all += containers
        for x in containers:
            if 'running' in (x.status or '').lower():
                x.kill()

        shell_url = _get_shell_url([
            "cd", f"/{os.environ['WEBSSH_CICD_WORKSPACE']}/cicd_instance_{site_name}", ";",
            "/usr/bin/python3",  "/opt/odoo/odoo", "-f", "--project-name", site_name, "restart",
        ])

    # return redirect(shell_url)

    return jsonify({
        'result': 'ok',
        'containers': [x.name for x in containers_all],
    })


@app.route("/shell_instance")
def shell_instance():
    name = request.args.get('name')
    site_name = name
    name += '_odoo'
    # kill existing container and start odoo with debug command
    containers = docker.containers.list(all=True, filters={'name': [name]})
    containers = [x for x in containers if x.name == name]
    shell_url = _get_shell_url([
        "cd", f"/{os.environ['WEBSSH_CICD_WORKSPACE']}/cicd_instance_{site_name}", ";",
        "/usr/bin/python3",  "/opt/odoo/odoo", "-f", "--project-name", site_name, "debug", "odoo_debug", "--command", "/odoolib/shell.py",
    ])
    # TODO make safe; no harm on system, probably with ssh authorized_keys

    return redirect(shell_url)

@app.route("/show_mails")
def show_mails():
    name = request.args.get('name')
    name += '_odoo'

    shell_url = _get_shell_url(["docker", "logs", "-f", name])
    return redirect(shell_url)

@app.route("/build_log")
def build_log():
    name = request.args.get('name')
    site = db.sites.find_one({'name': name})
    job = _get_jenkins_job(site['git_branch'])
    build = job.get_last_build_or_none()
    return render_template(
        'log_view.html',
        name=site['name'],
        site=site,
        build=build,
        output=build.get_console(),
    )

@app.route("/dump")
def backup_db():
    _set_marker_and_restart(
        request.args.get('name'),
        {
            'backup-db': request.args['dumpname'],
        }
    )
    return jsonify({
        'result': 'ok',
    })

@app.route("/build_again")
def build_again():
    if request.args.get('all') == '1':
        param_name = 'do-build-all'
    else:
        param_name = 'do-build'
    _set_marker_and_restart(
        request.args.get('name'),
        {
            param_name: True,
        }
    )
    return jsonify({
        'result': 'ok',
    })

def _get_db_conn():
    conn = psycopg2.connect(
        host=os.environ['DB_HOST'],
        port=int(os.environ['DB_PORT']),
        user=os.environ['DB_USER'],
        password=os.environ['DB_PASSWORD'],
        dbname="template1",
    )
    conn.autocommit = True
    return conn

@app.route("/delete")
def delete_instance():
    name = request.args.get('name')
    site = db.sites.find_one({'name': name})

    _delete_sourcecode(name)

    _delete_dockercontainers(name)

    conn = _get_db_conn()
    try:
        cr = conn.cursor()
        _drop_db(cr, name)
    finally:
        cr.close()
        conn.close()
        
    db.sites.remove({'name': name})
    db.updates.remove({'name': name})

    return jsonify({
        'result': 'ok',
    })

def _delete_dockercontainers(name):
    containers = docker.containers.list(all=True, filters={'name': [name]})
    for container in containers:
        if container.status == 'running':
            container.kill()
        container.remove(force=True)
    
def _delete_sourcecode(name):

    path = Path("/cicd_workspace") / f"cicd_instance_{name}"
    if not path.exists():
        return
    shutil.rmtree(path)

def _drop_db(cr, dbname):
    # Version 13:
    # DROP DATABASE mydb WITH (FORCE);
    dbnames = _get_all_databases(cr)
    if dbname not in dbnames:
        return
    cr.execute(f"ALTER DATABASE {dbname} CONNECTION LIMIT 0;")
    cr.execute("""
        SELECT pg_terminate_backend(pid)
        FROM pg_stat_activity
        WHERE datname = %s;
    """, (dbname,))
    cr.execute(f"DROP DATABASE {dbname}")

def _get_all_databases(cr):
    cr.execute("""
        SELECT d.datname as "Name"
        FROM pg_catalog.pg_database d
        ORDER BY 1;
    """)

    dbnames = [x[0] for x in cr.fetchall()]
    return dbnames

@app.route("/cleanup")
def cleanup():
    """
    Removes all unused source directories, databases
    and does a docker system prune.
    """
    conn = _get_db_conn()
    try:
        cr = conn.cursor()

        dbnames = _get_all_databases(cr)

        sites = set([x['name'] for x in db.sites.find({})])
        for dbname in dbnames:
            if dbname.startswith('template') or dbname == 'postgres':
                continue
            if dbname not in sites:

                _drop_db(cr, dbname)

        # Drop also old sourcecodes
        for dir in Path("/cicd_workspace").glob("cicd_instance_*"):
            instance_name = dir.name[len("cicd_instance_"):]
            if instance_name not in sites:
                _delete_sourcecode(instance_name)

        # remove artefacts from ~/.odoo/
        os.system("docker system prune -f -a")

    finally:
        cr.close()
        conn.close()

    return jsonify({'result': 'ok'})

@app.route("/get_resources")
def get_free_resources():
    return render_template(
        'resources.html',
        resources=_get_resources(),
    )

def _get_resources():
    for disk in Path("/display_resources").glob("*"):
        total, used, free = shutil.disk_usage(disk)
        yield {
            'name': disk.name,
            'total': total // (2**30),
            'used': used // (2**30),
            'free': free // (2**30),
            'used_percent': round(used / total * 100),
            'color': 'green' if round(used / total * 100) < 80 else 'red',
        }

    pass