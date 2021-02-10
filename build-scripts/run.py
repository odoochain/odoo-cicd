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

# -----------------------------------------------------------------

@cli.command()
@click.option("-k", "--key", type=click.Choice(['kept', 'live', 'demo']), required=True)
@click.option("-j", "--jira", is_flag=True)
@click.option("--dump-name", help="Name of the dump, that is restored")
def build(jira, key, dump_name):
    print(f"BUILDING for {branch} and key={key}")
    if jira:
        jira_wrapper = JiraWrapper(
            os.environ['JIRA_URL'],
            os.environ['JIRA_USER'],
            os.environ['JIRA_PASSWORD'],
        )
    else:
        jira_wrapper = JiraWrapper("", "", "")
    context = Context(
        env,
        jira_wrapper,
        Path(os.path.expanduser("~")) / '.odoo'
    )

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

    instance['git_sha'] = sha
    instance['git_branch'] = os.environ['GIT_BRANCH']
    augment_instance(context, instance)
    instance['author'] = author
    instance['desc'] = desc

    if key == 'demo':
        make_instance(context, instance, False)

    elif key == 'live':
        make_instance(context, instance, dump_name) # TODO parametrized from jenkins

    elif key == 'kept':
        update_instance(context, instance, dump_name)

    else:
        raise NotImplementedError(key)

class Context(object):
    def __init__(self, env, jira_wrapper, odoo_settings_file):
        self.jira_wrapper = jira_wrapper
        self.env = env
        self.cicd_url = os.environ['CICD_URL']
        self.odoo_settings = odoo_settings_file


if __name__ == '__main__':
    def _get_env():
        env_file = Path(sys.path[0]).parent / 'cicd-app' / '.env'
        load_dotenv(env_file)

        if not os.getenv("CICD_URL"):
            os.environ['CICD_URL'] = 'http://127.0.0.1:9999'
    _get_env()

    cli()
