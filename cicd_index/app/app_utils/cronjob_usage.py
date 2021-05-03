import logging
import pymongo
import time
import threading
from .. import db
from .tools import _get_db_conn
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
logger = logging.getLogger(__name__)


def _usages():
    while True:
        try:
            for site in db.sites.find({}):
                name = site['name']

                path = Path('/cicd_workspace') / name
                source_size = 0
                if path.exists():
                    source_size = sum(f.stat().st_size / 1024 / 1024 for f in path.glob('**/*') if f.is_file())

                settings = Path("/odoo_settings/.run") / name / 'settings'
                if settings.exists():
                    dbname = [x for x in settings.read_text().split("\n") if 'DBNAME=' in x][0].split("=")[1]
                    try:
                        conn = _get_db_conn
                        try:
                            cr = conn.cursor()
                            cr.execute("select pg_database_size('{dbname}')")
                            db_size = cr.fetchone()[0] / 1024 / 1024
                        finally:
                            conn.close()
                    except: db_size = 0

                db.sites.update_one({'_id': site['_id']}, {'$set': {
                    'db_size': db_size,
                    'source_size': source_size,
                }}, upsert=False)

        except Exception as ex:
            logger.error(ex)

        finally:
            time.sleep(60)


def start():
    logger.info("Starting job to build instances")
    t = threading.Thread(target=_usages)
    t.daemon = True
    t.start()
