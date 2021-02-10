#!/bin/env python3
import os
import re
import subprocess
import sys
from pathlib import Path
import json
import requests
import time
import arrow
from .jira import JiraWrapper
import click
from dotenv import load_dotenv
from datetime import datetime


@click.group()
def cli():
    pass


# -----------------------------------------------------------------
# From Environment
env = os.environ.copy()
env['DOCKER_CLIENT_TIMEOUT'] = "600"
env['COMPOSE_HTTP_TIMEOUT'] = "600"
env['PSYCOPG_TIMEOUT'] = "120"
author = os.getenv("GIT_AUTHOR_NAME") or ''
desc = os.getenv("GIT_DESC") or ''
sha = os.getenv("GIT_SHA")
branch = os.environ['GIT_BRANCH'].lower().replace("-", "_")

odoo_settings = Path(os.path.expanduser("~")) / '.odoo'
# -----------------------------------------------------------------

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

def _exec(cmd, needs_result=False):
    print_cmd = ' '.join(map(lambda x: f"'{x}'".format(x), cmd))
    print(f"Executing:\ncd '{os.getcwd()}';odoo {print_cmd}")

    method = 'check_output' if needs_result else 'check_call'
    res = getattr(subprocess, method)([
        "/opt/odoo/odoo",
    ] + cmd, env=env)
    if needs_result:
        return res.decode('utf-8')

def _notify_instance_updated(name, update_time, branch):
    print(f"notify_instance name: {name}, update_time: {update_time}, branch: {branch}")
    requests.get(cicd_url + "/notify_instance_updated", params={
        'name': name,
        'sha': sha,
        'update_time': update_time,
    }).json()
    requests.get(cicd_url + '/set_updating', params={'name': name, 'value': 0})
    jira_wrapper.comment(branch, f"Instance updated {name} in {update_time} seconds.")

def _make_instance_docker_configs(instance_name):
    file = odoo_settings / f'docker-compose.{instance_name}.yml'
    file.write_text("""
services:
    proxy:
        networks:
            - cicd_network
networks:
    cicd_network:
        external:
            name: {}
    """.format(os.environ["CICD_NETWORK"]))

def make_instance(name, key, git_branch, index, git_sha, use_dump, author, desc, use_previous_db=False):
    print(f"BUILD CONTROL: Making Instance for {name}")
    _make_instance_docker_configs(name)

    print("Reloading...")
    _exec(["-f", "reload", "-P", name, '-d', name, '--headless', '--devmode'])
    # print("Config...")
    # _exec(["-f", "--project-name", name, "config", "--full"])
    print(f"Calling register with branch {git_branch}")

    title = 'n/a'
    creator = 'n/a'
    try:
        if jira_wrapper:
            fields = jira_wrapper.infos(git_branch)
        title = fields.summary
        creator = fields.creator.displayName
    except Exception as ex:
        print(ex)

    if not git_branch:
        raise Exception("required git branch!")
    site = {
        'name': name,
        'date_registered': datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        'title': title,
        'key': key,
        'initiator': creator,
        'description': desc,
        'author': author,
        'git_branch': git_branch,
        'git_sha': git_sha,
        'diff_modules': [],
        'host_working_dir': Path(os.getcwd()).name,
        'index': int(index),
    }
    requests.post(cicd_url + '/register', json=site).raise_for_status()

    if use_dump:

        print(f"BUILD CONTROL: Restoring DB for {name} from {use_dump}")
        _exec(["-f", "--project-name", name, "restore", "odoo-db", use_dump])
        _exec(["-f", "--project-name", name, "remove-web-assets"])

    else:
        print(f"BUILD CONTROL: Resetting DB for {name}")
        _exec(["-f", "--project-name", name, "db", "reset"])

    _exec(["--project-name", name, "build"]) # build containers; use new pip packages

    started = arrow.get()
    _exec(["--project-name", name, "update"]) # odoo module updates
    _exec(["--project-name", name, "turn-into-dev", "turn-into-dev"])
    _exec(["--project-name", name, "set-ribbon", name])
    _notify_instance_updated(name, (arrow.get() - started).total_seconds(), git_branch)


@click.command()
@click.option("-k", "--key", type=click.Choice(['kept', 'live', 'demo']), required=True)
@click.option("-j", "--jira", is_flag=True)
@click.option("--dump-name", help="Name of the dump, that is restored")
def build(jira, key, dump_name):
    print(f"BUILDING for {branch} and key={key}")
    global jira_wrapper
    if jira:
        jira_wrapper = JiraWrapper(
            os.environ['JIRA_URL'],
            os.environ['JIRA_USER'],
            os.environ['JIRA_PASSWORD'],
        )

    # if not any(re.match(allowed, branch, re.IGNORECASE) for allowed in allowed):
    #    return

    if key in ['demo', 'live']:
        instance_info = requests.get(cicd_url + "/next_instance", params={
            'key': key,
            'branch': branch,
        }).json()
        print(f"new instance name: {instance_info['name']}")
        instance_name = instance_info['name']
        index = instance_info['index']
    elif key == 'kept':
        instance_name = f"{branch}_{key}_1"
        index = 1
    else:
        raise NotImplementedError()

    _setup_new_working_path(instance_name)

    if key == 'demo':
        make_instance(instance_name, key, branch, index, sha, False, author, desc)
    elif key == 'live':
        make_instance(instance_name, key, branch, index, sha, dump_name, author, desc) # TODO parametrized from jenkins
    elif key == 'kept':

        last_sha = requests.get(cicd_url + "/last_successful_sha", params={
            'name': instance_name,
        }).json()
        print(f"Result of asking for last_successful_sha: {last_sha}")
        if not last_sha.get('sha'):
            make_instance(instance_name, key, branch, index, sha, dump_name, author, desc, use_previous_db=True) # TODO parametrized from jenkins
        else:
            # mark existing instance as being updated
            requests.get(cicd_url + '/set_updating', params={'name': instance_name, 'value': 1})
            started = arrow.get()
            _exec(["--project-name", instance_name, "update", "--no-dangling-check", "--since-git-sha", last_sha['sha']])
            _notify_instance_updated(instance_name, (arrow.get() - started).total_seconds(), branch)

    else:
        raise NotImplementedError(key)


if __name__ == '__main__':
    def _get_env():
        env_file = Path(sys.path[0]).parent / 'cicd-app' / '.env'
        load_dotenv(env_file)

        if not os.getenv("CICD_URL"):
            os.environ['CICD_URL'] = 'http://127.0.0.1:9999'
    _get_env()
    cicd_url = os.environ['CICD_URL']

    jira_wrapper = None

    cli()
