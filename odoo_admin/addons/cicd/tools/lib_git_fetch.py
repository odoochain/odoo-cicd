import logging
import traceback
import arrow
from copy import deepcopy
from datetime import datetime
import time
from .tools import _get_main_repo
from .tools import _get_config

logger = logging.getLogger(__name__)


class NewBranch(Exception): pass

def _get_new_commits(odoo_repo):
    import pudb;pudb.set_trace()
        
    repo = _get_main_repo()

    for remote in repo.remotes:
        fetch_info = remote.fetch()
        for fi in fetch_info:
            name = fi.ref.name.split("/")[-1]
            if '/release/' in fi.ref.name:
                continue
            key = {
                'branch': name,
                'sha': str(fi.commit),
            }
            if not db.git_commits.find_one(key):
                data = deepcopy(key)
                data['triggered_update'] = False
                data['date'] = arrow.get().strftime("%Y-%m-%d %H:%M:%S")
                db.git_commits.update_one(key, {"$set": data}, upsert=True)
                # trigger onetime only for new branch
                try:
                    repo.git.checkout(name, force=True)
                    repo.git.pull()
                except Exception as ex:
                    logger.error(ex)
                    continue