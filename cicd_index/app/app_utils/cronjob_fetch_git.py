import logging
from copy import deepcopy
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

class NewBranch(Exception): pass

def _get_git_state():
        
    while True:
        try:
            repo = _get_main_repo()

            for remote in repo.remotes:
                fetch_info = remote.fetch()
                for fi in fetch_info:
                    name = fi.ref.name.split("/")[-1]
                    if '/release/' in fi.ref.name:
                        continue
                    try:
                        try:
                            repo.refs[name]
                        except IndexError:
                            raise NewBranch()
                            
                        else:
                            if repo.refs[remote.name + '/' + name].commit != fi.commit:
                                raise NewBranch()
                    except NewBranch:
                        key = {
                            'branch': name,
                            'sha': str(fi.commit),
                        }
                        data = deepcopy(key)
                        data['triggered_update'] = False
                        # trigger onetime only for new branch
                        db.git_commits.update_one(data, {"$set": data}, upsert=True)
                        repo.git.checkout(name, force=True)
                        repo.git.pull()

        except Exception as ex:
            import traceback
            msg = traceback.format_exc()
            logger.error(msg)

        finally:
            time.sleep(5)


def _make_new_instances():
        
    while True:
        try:
            for new_branch in db.git_commits.find({'triggered_update': False}):
                existing_site = db.sites.find_one({'name': new_branch['branch']})
                data = {
                    'name': new_branch['branch'],
                    'needs_build': True,
                }
                if not existing_site:
                    data['date_registered'] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            
                db.sites.update_one({
                    'name': new_branch['branch'],
                }, {'$set': data}, upsert=True)
                db.git_commits.update_one({'branch': new_branch['branch'], 'sha': new_branch['sha']}, {'$set': {'triggered_update': True}})

        except Exception as ex:
            import traceback
            msg = traceback.format_exc()
            logger.error(msg)

        finally:
            time.sleep(5)



def start():
    del_index_lock()

    logger.info("Starting job to fetch source code")
    t = threading.Thread(target=_get_git_state)
    t.daemon = True
    t.start()

    t = threading.Thread(target=_make_new_instances)
    t.daemon = True
    t.start()