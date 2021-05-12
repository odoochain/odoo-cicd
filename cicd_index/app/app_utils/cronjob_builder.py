import traceback
import logging
import arrow
import pymongo
import time
import threading
from .. import db
from .tools import _odoo_framework
from .tools import _get_config, _set_config
import os
import re
import subprocess
import sys
from pathlib import Path
import json
import requests
import time
import arrow
import click
from .tools import _get_repo
from dotenv import load_dotenv
from datetime import datetime
from .tools import _store
from git import Repo
from .tools import store_output, get_output
from .tools import update_instance_folder
from .tools import _get_instance_config
from .. import rolling_log_dir
logger = logging.getLogger(__name__)

threads = {} # for multitasking

# context.jira_wrapper.comment(
#     instance['git_branch'],
#     f"Instance updated {name} in {duration} seconds."
# )

def _make_instance_docker_configs(site):
    instance_name = site['name']
    odoo_settings = Path("/odoo_settings")  # e.g. /home/odoo/.odoo
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
    """.format(os.environ["CICD_NETWORK_NAME"]))

    (odoo_settings / f'settings.{instance_name}').write_text("""
DEVMODE=1
PROJECT_NAME={}
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
        instance_name,
        os.environ['DUMPS_PATH'],
        os.environ['DB_HOST'],
        os.environ['DB_USER'],
        os.environ['DB_PASSWORD'],
        os.environ['DB_PORT'],
    ))

def notify_instance_updated(site):
    repo = _get_repo(site['name'])
    sha = str(repo.active_branch.commit)
    info = {
        'name': site['name'],
        'sha': sha,
        'git_author': repo.active_branch.commit.author.name,
        'git_desc': repo.active_branch.commit.summary,
        'git_authored_date': arrow.get(repo.active_branch.commit.authored_date).strftime("%Y-%m-%d %H:%M:%S"),
    }
    info['date'] = arrow.get().to('utc').strftime("%Y-%m-%d %H:%M:%S")
    db.updates.insert_one(info)
    info.pop("_id")

    site = db.sites.find_one({'name': site['name']})
    if site:
        db.sites.update_one(
            {'_id': site['_id']}, 
            {"$set": info },
            upsert=False
        )

def _last_success_full_sha(site):
    info = {'name': site['name']}
    updates = list(db.updates.find(info).sort([("date", pymongo.DESCENDING)]).limit(1))
    if updates:
        return updates[0]['sha']


def make_instance(site, use_dump):
    logger.info(f"Make instance for {site}")
    settings = _get_instance_config(site['name'])
    _make_instance_docker_configs(site)

    store_output(site['name'], 'meta', (
        f"Date: {arrow.get()}"
    ))

    output = _odoo_framework(
        site['name'], 
        ["reload", '-d', site['name'], '--headless', '--devmode'],
        start_rolling_new=True
    )
    store_output(site['name'], 'reload', output)

    output = _odoo_framework(
        site['name'], 
        ["build"], # build containers; use new pip packages
    )
    store_output(site['name'], 'build', output)

    dump_date, dump_name = None, None
    if use_dump:
        logger.info(f"BUILD CONTROL: Restoring DB for {site['name']} from {use_dump}")
        _odoo_framework(site, ["restore", "odoo-db", use_dump])
        logger.info(f"Restoring dump {site['name']} finished")
        _odoo_framework(site, ["remove-web-assets"])
        dump_file = Path("/opt/dumps") / use_dump
        dump_date = arrow.get(dump_file.stat().st_mtime).to('UTC').strftime("%Y-%m-%d %H:%M:%S")
        dump_name = use_dump

        _store(site['name'], {
            'dump_date': dump_date,
            'dump_name': dump_name,
            }
        )

    else:
        logger.info(f"BUILD CONTROL: Resetting DB for {site['name']}")
        if settings['DBNAME']:
            _odoo_framework(site, ["db", "reset", dbname])

    output = _odoo_framework(site, ["update"])
    store_output(site['name'], 'update', output)

    _odoo_framework(site, ["turn-into-dev", "turn-into-dev"])

    _odoo_framework(site, ["set-ribbon", site['name']])


def fix_ownership():
    user = os.environ['HOST_SSH_USER']
    pass
    # _execute_shell
    # os.system(f"chown {user}:{user} /odoo_settings -R")


def build_instance(site):
    try:
        logger.info(f"Building instance {site['name']}")
        fix_ownership()
        started = arrow.get()
        settings = _get_instance_config(site['name'])
        _store(site['name'], {
            "build_started": started.to("utc").strftime("%Y-%m-%d %H:%M:%S"),
            })
        try:
            dump_name = site.get('dump') or os.getenv("DUMP_NAME")

            if site.get("build_mode"):
                _make_instance_docker_configs(site)
                logger.info(f"Reloading {site['name']}")
                _odoo_framework(site, 
                    ["reload", '-d', site['name'], '--headless', '--devmode']
                )
                logger.info(f"Downing {site['name']}")
                try:
                    _odoo_framework(site, ["down"])
                except Exception as ex:
                    logger.warn(ex)

                logger.info(f"Building {site['name']}")
                _odoo_framework(site, ["build"])
                logger.info(f"Upping {site['name']}")
                _odoo_framework(site, ["up", "-d"])
                logger.info(f"Upped {site['name']}")

            else:

                last_sha = _last_success_full_sha(site)
                if site.get('reset-db'):
                    if settings['DBNAME']:
                        _odoo_framework(site, ['db', 'reset', settings['DBNAME']])

                if not last_sha or site.get('force_rebuild'):
                    logger.debug(f"Make new instance: force rebuild: {site.get('force_rebuild')} / last sha: {last_sha and last_sha.get('sha')}")
                    make_instance(site, dump_name)
                else:
                    if site.get('do-build-all'):
                        output = _odoo_framework(
                            site, 
                            ["update", "--no-dangling-check", "--i18n"]
                        )
                    else:
                        output = _odoo_framework(
                            site, 
                            ["update", "--no-dangling-check", "--since-git-sha", last_sha, "--i18n"]
                        )

                    store_output(site['name'], 'update', output)

                    _odoo_framework(site, ["up", "-d"])

            notify_instance_updated(site)

            success = True
        except Exception as ex:
            success = False
            msg = traceback.format_exc()
            logger.error(msg)
            store_output(site['name'], 'error', str(msg))
        
        _store(site['name'], {
            'is_building': False,
            'needs_build': False,
            'success': success,
            'force_rebuild': False,
            'do-build-all': False,
            'reset-db': False,
            'updated': datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            'duration': round((arrow.get() - started).total_seconds(), 0),
            'build_mode': False,
        })

    except Exception as ex:
        logger.error(ex)
    finally:
        _store(site['name'], {
            'is_building': False,
            'needs_build': False,
        })
        assert not db.sites.find_one({'name': site['name']})['needs_build']
        del threads[site['name']]


def _build():
    ids = map(lambda x: x['_id'], db.sites.find({}))
    for id in ids:
        db.sites.update({'_id': id}, {"$set": {'is_building': False}})  # update with empty {} did not work

    while True:
        try:
            concurrent_threads = _get_config('concurrent_builds', 5)

            count_active = len([x for x in threads.values() if x.is_alive()])
            logger.debug(f"Active builds: {count_active}, configured max builds: {concurrent_threads}")

            sites = list(db.sites.find({'needs_build': True}))
            sites = [x for x in sites if not x.get('is_building')]
            sites = [x for x in sites if not x.get('archive')]
            if not sites:
                logger.debug("Nothing to build")
            for site in sites:
                if not threads.get(site['name']) or not threads[site['name']].is_alive():
                    if count_active < concurrent_threads:
                        for key in ['reload', 'name', 'update', 'build', 'last_error']:
                            store_output(site['name'], key, "")

                        rolling_file = rolling_log_dir / site['name']
                        if rolling_file.exists():
                            rolling_file.write_text(f"_____ _____Started new build: {arrow.get()}")

                        try:
                            update_instance_folder(site['name'])
                        except Exception as ex:
                            msg = traceback.format_exc()
                            store_output(site['name'], 'last_error', msg)
                            _store(site['name'], {'is_building': False, 'needs_build': False, 'success': False})
                            continue

                        _store(site['name'], {'is_building': True})
                        thread = threading.Thread(target=build_instance, args=(site,))
                        threads[site['name']] = thread
                        thread.start()
                        count_active += 1

        except Exception as ex:
            msg = traceback.format_exc()
            logger.error(msg)

        finally:
            time.sleep(1)


def start():
    logger.info("Starting job to build instances")
    t = threading.Thread(target=_build)
    t.daemon = True
    t.start()
