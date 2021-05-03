import logging
from datetime import datetime
import time
import git
import threading
from . import WORKSPACE
from . import URL
from .. import db
from .. import MAIN_FOLDER_NAME
import subprocess
from pathlib import Path
from .tools import _get_main_repo
import os
import shutil
from .tools import update_instance_folder
logger = logging.getLogger(__name__)



def del_index_lock():
    paths = []
    paths.append(WORKSPACE / MAIN_FOLDER_NAME)
    for site in db.sites.find({}):
        WORKSPACE / site['name']
        paths.append(WORKSPACE / MAIN_FOLDER_NAME)

    for path in paths:
        idxfile = path / '.git' / 'index.lock'
        if idxfile.exists():
            idxfile.unlink()

def _get_git_state():
    del_index_lock()
        
    while True:
        try:
            repo = _get_main_repo()

            new_branches = []
            for remote in repo.remotes:
                fetch_info = remote.fetch()
                for fi in fetch_info:
                    name = fi.ref.name.split("/")[-1]
                    try:
                        repo.refs[name]
                    except IndexError:
                        new_branches.append(name)
                    else:
                        if repo.refs[name].commit != fi.commit:
                            new_branches.append(name)

            logger.debug(f"New Branches detected: {new_branches}")
            for branch in new_branches:
                update_instance_folder(branch)
            
                db.sites.update_one({
                    'name': branch,
                }, {'$set': {
                    'name': branch,
                    'needs_build': True,
                    'git_sha': str(commit),
                    'git_author': commit.author.name,
                    'git_desc': commit.message,
                    'date_registered': datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                }
                }, upsert=True)

        except Exception as ex:
            logger.error(ex)

        finally:
            time.sleep(5)


def start():
    logger.info("Starting job to fetch source code")
    t = threading.Thread(target=_get_git_state)
    t.daemon = True
    t.start()