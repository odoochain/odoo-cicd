import arrow
from datetime import datetime
import requests
from functools import partial
from pathlib import Path
import subprocess
import os

def _setup_new_working_path(instance_name):
    new_path = Path(os.getcwd()).parent / f'cicd_instance_{instance_name}'
    new_path.mkdir(exist_ok=True) # set later False; avoids thresholing
    subprocess.check_call([
        '/usr/bin/rsync',
        str(os.getcwd()) + "/",
        str(new_path) + "/",
        '-ar',
        '--delete-after',
    ])
    print(f"Changing working directory to: {new_path}")
    os.chdir(new_path)
    subprocess.check_call([
        '/usr/bin/git',
        'repack',
        '-a',
        '-d',
        '--depth=250',
        '--window=250',
    ])

def _exec(context, cmd, needs_result=False):
    print_cmd = ' '.join(map(lambda x: f"'{x}'".format(x), cmd))
    print(f"Executing:\ncd '{os.getcwd()}';odoo {print_cmd}")

    method = 'check_output' if needs_result else 'check_call'
    res = getattr(subprocess, method)([
        "/opt/odoo/odoo",
    ] + cmd, env=context.env)
    if needs_result:
        return res.decode('utf-8')

def _notify_instance_updated(context, instance, update_time):
    name = instance['name']
    print(f"notify_instance name: {name}, update_time: {update_time}, branch: {instance['git_branch']}")
    requests.get(context.cicd_url + "/notify_instance_updated", params={
        'name': name,
        'sha': instance['git_sha'],
        'update_time': update_time,
    }).json()
    requests.get(context.cicd_url + '/set_updating', params={'name': name, 'value': 0})
    context.jira_wrapper.comment(
        instance['git_branch'],
        f"Instance updated {name} in {update_time} seconds."
    )

def _make_instance_docker_configs(context, instance):
    instance_name = instance['name']
    file = context.odoo_settings / f'docker-compose.{instance_name}.yml'
    file.parent.mkdir(exist_ok=True)
    file.write_text("""
services:
    proxy:
        networks:
            - cicd_network
networks:
    cicd_network:
        external:
            name: {}
    """.format(os.environ["CICD_NETWORK_NAME"]))

    (context.odoo_settings / f'settings.{instance_name}').write_text("""
DEVMODE=1
DUMPS_PATH={}
RUN_PROXY_PUBLISHED=0
RUN_ODOO_CRONJOBS=0
RUN_ODOO_QUEUEJOBS=0
RUN_CRONJOBS=0
RUN_CUPS=0
RUN_POSTGRES=0

DB_HOST={}
DB_USER={}
DB_PWD={}
DB_PORT={}
""".format(
        os.environ['DUMPS_PATH'],
        os.environ['DB_HOST'],
        os.environ['DB_USER'],
        os.environ['DB_PASSWORD'],
        os.environ['DB_PORT'],
    ))

def augment_instance(context, instance):
    title = 'n/a'
    creator = 'n/a'
    try:
        fields = context.jira_wrapper.infos(instance['git_branch'])
        title = fields.summary
        creator = fields.creator.displayName
    except Exception as ex:
        print(ex)
    instance['title'] = title
    instance['initiator'] = creator

def update_instance(context, instance, dump_name):
    print(f"Updating instance {instance['name']}")
    _setup_new_working_path(instance['name'])
    last_sha = requests.get(context.cicd_url + "/last_successful_sha", params={
        'name': instance['name'],
    }).json()
    print(f"Result of asking for last_successful_sha: {last_sha}")
    if not last_sha.get('sha'):
        make_instance(context, instance, dump_name, use_previous_db=True) # TODO parametrized from jenkins
    else:
        # mark existing instance as being updated
        requests.get(context.cicd_url + '/set_updating', params={
            'name': instance['name'], 'value': 1
        })
        started = arrow.get()
        _exec(context, ["--project-name", instance['name'], "update", "--no-dangling-check", "--since-git-sha", last_sha['sha']])
        _notify_instance_updated(
            context, instance, (arrow.get() - started).total_seconds(),
        )

def make_instance(context, instance, use_dump, use_previous_db=False):
    _setup_new_working_path(instance['name'])
    print(f"BUILD CONTROL: Making Instance for {instance['name']}")
    _make_instance_docker_configs(context, instance)

    def e(cmd, needs_result=False):
        cmd = ["-f", "--project-name", instance['name']] + cmd
        return _exec(context, cmd, needs_result)

    print("Reloading...")
    e(["reload", '-d', instance['name'], '--headless', '--devmode'])
    print(f"Calling register with branch {instance['git_branch']}")

    if not instance['git_branch']:
        raise Exception("required git branch!")
    instance['date_registered'] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    requests.post(context.cicd_url + '/register', json=instance).raise_for_status()

    if use_dump:
        print(f"BUILD CONTROL: Restoring DB for {instance['name']} from {use_dump}")
        e(["restore", "odoo-db", use_dump])
        e(["remove-web-assets"])

    else:
        print(f"BUILD CONTROL: Resetting DB for {instance['name']}")
        e(["db", "reset"])

    e(["build"]) # build containers; use new pip packages

    started = arrow.get()
    e(["update"]) # odoo module updates
    e(["turn-into-dev", "turn-into-dev"])
    e(["set-ribbon", instance['name']])
    _notify_instance_updated(context, instance, (arrow.get() - started).total_seconds())
