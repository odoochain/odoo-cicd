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
from lib.jira import JiraWrapper
import click
from dotenv import load_dotenv
from datetime import datetime
from lib.build import make_instance
from lib.build import update_instance
from lib.build import augment_instance


@click.group()
def cli():
    pass

def _export_git_values():

    def g(v):
        git = ['/usr/bin/git', 'show', '-s']
        return subprocess.check_output(git + [f'--pretty={v}']).decode('utf-8').strip()

    os.environ['GIT_AUTHOR_NAME'] = g("%an")
    os.environ['GIT_DESC'] = g("%s")
    os.environ['GIT_SHA'] = g("%H")


# -----------------------------------------------------------------
# From Environment
_export_git_values()
env = os.environ.copy()
env['NO_PROXY'] = "*"
env['DOCKER_CLIENT_TIMEOUT'] = "600"
env['COMPOSE_HTTP_TIMEOUT'] = "600"
env['PSYCOPG_TIMEOUT'] = "120"
author = os.getenv("GIT_AUTHOR_NAME") or ''
desc = os.getenv("GIT_DESC") or ''
sha = os.getenv("GIT_SHA")
branch = os.environ['GIT_BRANCH'].lower().replace("-", "_")

# -----------------------------------------------------------------


def _get_jira_wrapper(use_jira):
    if use_jira:
        jira_wrapper = JiraWrapper(
            os.environ['JIRA_URL'],
            os.environ['JIRA_USER'],
            os.environ['JIRA_PASSWORD'],
        )
    else:
        jira_wrapper = JiraWrapper("", "", "")
    return jira_wrapper


@cli.command()
def clearflags(self):
    # clear reset-db-at-next-build
    context = Context(False)
    print("Clearing flags")
    requests.get(context.cicd_url + "/update/branch", params={
        'git_branch': branch,
        'reset-db-at-next-build': False,
    })


@cli.command()
@click.option("-k", "--key", type=click.Choice(['kept', 'live', 'demo']), required=True)
@click.option("-j", "--jira", is_flag=True)
def build(jira, key):
    dump_name = os.environ['DUMP_NAME']
    workspace = os.environ['CICD_WORKSPACE']
    if not workspace:
        print("Please provide CICD_WORKSPACE in environment!")
        sys.exit(-1)

    print(f"BUILDING for {branch} and key={key}; workspace: {workspace}")
    context = Context(jira)
    if workspace:
        context.workspace = Path(workspace)

    # if not any(re.match(allowed, branch, re.IGNORECASE) for allowed in allowed):
    #    return

    if key in ['demo', 'live']:
        instance = requests.get(context.cicd_url + "/next_instance", params={
            'key': key,
            'branch': branch,
        }).json()
        print(f"new instance name: {instance['name']}")
    elif key == 'kept':
        instance = {
            'name': f"{branch}_{key}_1",
            'index': 1,
        }
    else:
        raise NotImplementedError()

    instance['key'] = key
    instance['git_sha'] = sha
    instance['git_branch'] = os.environ['GIT_BRANCH']
    augment_instance(context, instance)
    instance['author'] = author
    instance['desc'] = desc

    print("try to get build informations")
    record_branch = requests.get(context.cicd_url + "/data/branches", params={
        'git_branch': instance['git_branch'],
    }).json()
    force_rebuild = False
    if record_branch:
        record_branch = record_branch[0]
        if record_branch.get('reset-db-at-next-build'):
            force_rebuild = True
        if record_branch.get('dump'):
            dump_name = record_branch['dump']

    if key == 'demo':
        make_instance(context, instance, False)

    elif key == 'live':
        make_instance(context, instance, dump_name) # TODO parametrized from jenkins

    elif key == 'kept':
        update_instance(context, instance, dump_name, force_rebuild=force_rebuild)

    else:
        raise NotImplementedError(key)

class Context(object):
    def __init__(self, use_jira):
        self.jira_wrapper = _get_jira_wrapper(use_jira)
        self.env = env
        self.cicd_url = os.environ['CICD_URL']
        if self.cicd_url.endswith("/"):
            self.cicd_url = self.cicd_url[:-1]
        self.cicd_url += '/cicd'
        self.odoo_settings = Path(os.path.expanduser("~")) / '.odoo'
        self.workspace = Path(os.getcwd()).parent


if __name__ == '__main__':

    def _get_env():
        env_file = Path(sys.path[0]).parent / '.env'
        load_dotenv(env_file)

        if not os.getenv("CICD_URL"):
            os.environ['CICD_URL'] = 'http://127.0.0.1:9999'

        print(f"Using cicd app on {os.environ['CICD_URL']}")
    _get_env()

    cli()
