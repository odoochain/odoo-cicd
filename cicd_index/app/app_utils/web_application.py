from .. import MAIN_FOLDER_NAME
import time
import traceback
import subprocess
from functools import partial
import threading
import tempfile
import humanize
from flask import Flask, request, send_from_directory
import os
import base64
import arrow
from .tools import _get_host_path
from .tools import _delete_sourcecode, get_output, write_rolling_log
from .tools import _get_db_conn
from pathlib import Path
from flask import redirect
from flask import request
from flask import jsonify
from .. import app
from .. import login_required
from flask import render_template
from flask import make_response
from .tools import _format_dates_in_records
from .tools import _get_resources
from .. import db
from .tools import _odoo_framework
from .tools import _drop_db
from .tools import _validate_input
from .tools import _get_all_databases
from .tools import _get_docker_state
from .tools import _delete_dockercontainers
from bson import ObjectId
import logging
from datetime import datetime
import docker as Docker
from .tools import get_output
from .tools import update_instance_folder
from .. import rolling_log_dir
import flask_login
import shutil
logger = logging.getLogger(__name__)

docker = Docker.from_env()

@app.route('/')
@login_required
def index_func():

    return render_template(
        'index.html',
        DATE_FORMAT=os.environ['DATE_FORMAT'].replace("_", "%"),
    )

def _get_dump_files_of_dir(path, relative_to):
    dump_names = sorted([x for x in path.glob("*")])

    def _get_value(filename):
        date = arrow.get((path / filename).stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        size = "?"
        if filename.exists():
            size = filename.stat().st_size
            size = humanize.naturalsize(size)
        return f"{filename.relative_to(relative_to)} [{date}] {size}"

    def _get_name(filepath):
        if not relative_to:
            return filepath
        res = Path(filepath).relative_to(relative_to)
        return res

    dump_names = [{'id': str(_get_name(x)), 'value': _get_value(x)} for x in dump_names]
    return dump_names

@app.route("/possible_dumps")
def possible_dumps():
    path = Path(os.environ['DUMPS_PATH_MAPPED'])
    dump_names = _get_dump_files_of_dir(path, path)
    return jsonify(dump_names)

@app.route("/possible_input_dumps")
def possible_input_dumps():
    path = Path(os.environ['INPUT_DUMPS_PATH_MAPPED'])
    dump_names = []
    for subdir in path.glob("*"):
        if subdir.is_dir():
            dump_names += _get_dump_files_of_dir(subdir, relative_to=path)
    return jsonify(dump_names)

@app.route("/transform_input_dump")
def transform_input_dump():
    dump = Path(request.args['dump'])
    erase = request.args['erase'] == '1'
    anonymize = request.args['anonymize'] == '1'
    site = 'master'
    rolling_file= rolling_log_dir / f"{site}_{arrow.get().strftime('%Y-%m-%d_%H%M%S')}"

    def do():
        instance_folder = Path("/cicd_workspace") / f"prepare_dump_{Path(tempfile.mktemp()).name}"
        try:
            # reverse lookup the path
            real_path = _get_host_path(Path("/input_dumps") / dump.parent) / dump.name

            def of(*args):
                _odoo_framework(instance_folder.name, list(args), rolling_file_name=rolling_file, instance_folder=instance_folder)

            write_rolling_log(rolling_file, "Preparing instance folder")
            source = str(Path("/cicd_workspace") / "master") + "/"
            dest = str(instance_folder) + "/"
            write_rolling_log(rolling_file, f"rsync from {source} to {dest}")
            subprocess.check_call([
                "rsync", source, dest,
                "-ar",
                "--exclude=.odoo"
            ])
            # #update_instance_folder(site, rolling_file, instance_folder=instance_folder)
            of("reload")

            # to avoid orphan messages, that return error codes although warning
            write_rolling_log(rolling_file, f"Starting local postgres")
            of("up", "-d", 'postgres')
            write_rolling_log(rolling_file, f"Waiting 10 seconds for postgres to start")

            of("restore", "odoo-db", str(real_path))
            suffix =''
            if erase:
                of("cleardb")
                suffix += '.cleared'
            if anonymize:
                of("anonymize")
                suffix += '.anonym'
            of("backup", "odoo-db", dump.name + suffix + '.cicd_ready')
            of("down")
        except Exception as ex:
            msg = traceback.format_exc()
            write_rolling_log(rolling_file, msg)
        finally:
            if instance_folder.exists(): 
                shutil.rmtree(instance_folder)

    t = threading.Thread(target=do)
    t.start()

    return jsonify({
        'live_url': "/cicd/live_log?name=" + rolling_file.name
    })

@app.route("/turn_into_dev")
def _turn_into_dev():
    if not request.args.get('site'):
        raise Exception('site missing')
    site = db.sites.find_one({'name': request.args.get('site')})
    if site:
        site = site['name']
        _reload_instance(site)
        _odoo_framework(site, ["turn-into-dev", "turn-into-dev"])
    return jsonify({'result': 'ok'})

def _reload_instance(site):
    _odoo_framework(site, ["reload", "-d", site])

    
@app.route('/trigger/rebuild')
def trigger_rebuild():
    site = db.sites.find_one({'name': request.args['name']})
    db.updates.remove({'name': site['name']})
    data = {
        'needs_build': True,
        'build_mode': 'reset',
        'docker_no_cache': request.args.get('no_cache') == '1',
    }
    if request.args.get('dump'):
        data['dump'] = request.args['dump']

    db.sites.update_one({'name': request.args.get('name')}, {'$set': data}, upsert=False)
    return jsonify({
        'result': 'ok',
    })

@app.route("/data/site/live_values")
def site_jenkins():
    sites = list(db.sites.find({}, {
        'name': 1, 'is_building': 1, 'duration': 1,
        'docker_state': 1, 'build_state': 1, 'success': 1,
        'needs_build': 1, 'db_size_humanize': 1, 'source_size_humanize': 1
        }))
    for site in sites:
        site['id'] = site['_id']
        site['build_state'] = _get_build_state(site)
    return jsonify({
        'sites': sites,
    })

@app.route("/data/sites", methods=["GET", "POST"])
def data_variants():
    _filter = {}

    user = flask_login.current_user
        
    if request.args.get('git_branch', None):
        _filter['git_branch'] = request.args['git_branch']
    if request.args.get('name', None):
        _filter['name'] = request.args['name']

    sites = list(db.sites.find(_filter))

    sites = _format_dates_in_records(sites)
    sites = sorted(sites, key=lambda x: x.get('name'))
    if not request.args.get('name'):
        if request.args.get('archived') == '1':
            sites = [x for x in sites if x.get('archive')]
        else:
            sites = [x for x in sites if not x.get('archive')]

    for site in sites:
        site['id'] = site['_id']
        site['update_in_progress'] = False
        site['repo_url'] = f"{os.environ['REPO_URL']}/-/commit/{site.get('git_sha')}"
        site['build_state'] = _get_build_state(site)
        site['duration'] = site.get('duration', 0)

    if user.is_authenticated and not user.is_admin:
        user_db = db.users.find_one({'login': user.id})
        sites = [x for x in sites if x['name'] in user_db.get('sites')]

    sites = list(sorted(sites, key=lambda x: (1 if x.get('archive') else 0, x.get('name').lower())))

    return jsonify(sites)

def _get_build_state(site):
    if site.get('is_building'):
        return "Building...."
    if site.get('needs_build'):
        return 'Scheduled'
    if 'success' in site:
        return 'SUCCESS' if site['success'] else 'FAILED'
    return ''

@app.route('/update/site', methods=["GET", "POST"])
def update_site():
    if request.method == 'POST':
        data = request.form
    else:
        data = request.args
    data = _validate_input(data, int_fields=['archive'])
    if '_id' not in data and 'git_branch' in data:
        branch_name = data.pop('git_branch')
        site = db.sites.find_one({'git_branch': branch_name})
        if not site:
            return jsonify({'result': 'not_found', 'msg': "Site not found"})
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
    from .web_instance_control import _restart_docker
    name = request.args['name']
    docker_state = _get_docker_state(name)
    logger.info(f"Opening user interface of cicd instance {name}; current docker state: {docker_state}")
    if not docker_state:
        _restart_docker(name, kill_before=False)

    response = make_response(
        render_template(
            'start_cicd.html',
            initial_path=request.args.get('initial_path') or '/web'
        ),
    )
    response.set_cookie('delegator-path', name)
    response.set_cookie('frontend_lang', '', expires=0)
    response.set_cookie('im_livechat_history', '', expires=0)
    response.set_cookie('session_id', "", expires=0)
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
    name += '_' + request.args.get('service')
    containers = docker.containers.list(all=True, filters={'name': [name]})
    containers = [x for x in containers if x.name == name]
    shell_url = _get_shell_url(["docker", "logs", "-f", containers[0].id])
    return redirect(shell_url)

@app.route("/debug_instance")
def debug_instance():
    name = request.args.get('name')
    site_name = name
    name += '_odoo'

    _odoo_framework(site_name, ['kill', 'odoo'])
    _odoo_framework(site_name, ['kill', 'odoo_debug'])

    shell_url = _get_shell_url([
        "cd", f"/{os.environ['WEBSSH_CICD_WORKSPACE']}/{site_name}", ";",
        "/usr/bin/python3",  "/opt/odoo/odoo", "-f", "--project-name", site_name, "debug", "odoo", "--command", "/odoolib/debug.py",
    ])
    # TODO make safe; no harm on system, probably with ssh authorized_keys

    return redirect(shell_url)
    
@app.route("/get_resources")
def get_free_resources():
    return render_template(
        'resources.html',
        resources=_get_resources(),
    )

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

        sites = set([x['name'] for x in db.sites.find({}) if not x.get('archived')])
        for dbname in dbnames:
            if dbname.startswith('template') or dbname == 'postgres':
                continue

            # critical: reverse dbname to instance name
            def match(site, dbname):
                ignored_chars = "-!@#$%^&*()_-+=][{}';:,.<>/"
                site = site.lower()
                dbname = dbname.lower()
                for c in ignored_chars:
                    site = site.replace(c, '')
                    dbname = dbname.replace(c, '')
                return dbname == site

            if not [x for x in sites if match(x, dbname)]:
                _drop_db(cr, dbname)


        # Drop also old sourcecodes
        for dir in Path("/cicd_workspace").glob("*"):
            if dir.name == MAIN_FOLDER_NAME:
                continue
            instance_name = dir.name
            if instance_name not in sites:
                _delete_sourcecode(instance_name)

        # remove artefacts from ~/.odoo/
        os.system("docker system prune -f -a")

        # drop old docker containers
        cicd_prefix = os.environ['CICD_PREFIX']
        containers = docker.containers.list(all=True, filters={'label': f'ODOO_CICD={cicd_prefix}'})
        for container in containers:
            site_name = container.labels.get('ODOO_CICD_INSTANCE_NAME', '')
            if not site_name: continue
            if site_name not in sites:
                if container.status == 'running':
                    container.kill()
                container.remove(force=True)

    finally:
        cr.close()
        conn.close()

    return jsonify({'result': 'ok'})

    
    
    
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
        
    db.sites.update_one(
        {'_id': site['_id']},
        {"$set": {'archive': True}}
        )
    db.updates.remove({'name': name})

    return jsonify({
        'result': 'ok',
    })

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
    if site.get('is_building'):
        return redirect("/cicd/live_log?name=" + site['name'])
    else:
        output = []
        for heading in [
            ("Meta", 'meta'),
            ("Reload", 'reload'),
            ("Build", 'build'),
            ("Last Update", 'update'),
            ("Last Error", 'last_error'),
        ]:
            output.append(f"<h1>{heading[0]}</h1>")
            output.append(get_output(site['name'], heading[1]))

        return render_template(
            'log_view.html',
            name=site['name'],
            site=site,
            build_status="SUCCESS" if site.get('success') else "FAILURE",
            duration=site.get('duration', 0),
            output="<hr/>".join(output),
        )

@app.route("/dump")
def backup_db():
    site = db.sites.find_one({'name': request.args.get('name')})
    dump_name = request.args.get('dumpname')
    _odoo_framework(site, ['backup', 'odoo-db', dump_name])
    return jsonify({
        'result': 'ok',
    })

@app.route("/build_again")
def build_again():
    if request.args.get('all') == '1':
        mode = 'update-all-modules'
    else:
        mode = 'update-recent'
    db.sites.update_one({'name': request.args.get('name')}, {'$set': {
        'build_mode': mode,
        'needs_build': True,
    }}, upsert=False)
    return jsonify({
        'result': 'ok',
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
        "cd", f"/{os.environ['WEBSSH_CICD_WORKSPACE']}/{site_name}", ";",
        "/usr/bin/python3",  "/opt/odoo/odoo", "-f", "--project-name", site_name, "debug", "odoo_debug", "--command", "/odoolib/shell.py",
    ])
    # TODO make safe; no harm on system, probably with ssh authorized_keys

    return redirect(shell_url)


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


@app.route('/start_all')
def start_all_instances():
    from .web_instance_control import _restart_docker
    _restart_docker(None, kill_before=False)
    return jsonify({
        'result': 'ok',
    })

@app.route('/restart_delegator')
def restart_delegator():
    docker_project_name = os.environ['PROJECT_NAME']
    names = []
    names.append(f"{docker_project_name}_cicd_delegator")
    names.append(f"{docker_project_name}_nginx")
    for name in names:
        containers = docker.containers.list(all=True, filters={'name': [name]})
        for container in containers:
            try:
                container.stop()
            except Exception:
                logger.info(f"Container not stoppable - maybe ok: {container.name}")
            container.start()
    return jsonify({
        'result': 'ok',
    })


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



@app.route("/start_info")
def start_info():
    u = flask_login.current_user
    return jsonify({
        'is_admin': u.is_authenticated and u.is_admin
    })

@app.route("/reload_restart")
def reload_restart():
    site = db.sites.find_one({'name': request.args['name']})
    data = {
        'needs_build': True,
        'build_mode': 'reload_restart',
    }
    db.sites.update_one({'name': site['name']}, {"$set": data}, upsert=True)

    return jsonify({
        'result': 'ok',
    })

@app.route("/make_custom_instance")
def make_custom_instance():
    name = request.args['name']
    assert name
    site = db.sites.find_one({'name': name})
    if site:
        raise Exception("site already exists")
    data = {
        'name': name,
        'needs_build': True,
        'force_rebuild': True,
    }
    db.sites.update_one({'name': name}, {"$set": data}, upsert=True)

    return jsonify({
        'result': 'ok',
    })

@app.route("/live_log")
def livelog():
    name = request.args['name']
    return render_template(
        'live_log.html',
        site=name,
    )

@app.route("/live_log/new_lines")
def fetch_new_lines():
    MAX_LINES = 1000
    name = request.args.get('name')
    name = name.replace('/', '_')
    file = rolling_log_dir / name
    result = {
        'content': [],
        'next_line_number': 0,
    }

    next_line_number = int(request.args.get('next_line_number') or '0')
    if file.exists():
        content = file.read_text().strip().split("\n")
    else:
        content = []
    if next_line_number > len(content) + 1:
        next_line_number = 0
    content = content[next_line_number:next_line_number + MAX_LINES]
    lines = len(content)
    result['content'] = content
    result['next_line_number'] = next_line_number + lines

    return jsonify(result)