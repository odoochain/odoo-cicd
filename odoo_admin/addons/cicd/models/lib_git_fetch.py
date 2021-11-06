import logging
import traceback
import arrow
from copy import deepcopy
from datetime import datetime
import time
from .tools import _get_main_repo
from . import BUILDING_LOCK
from .tools import _get_config

logger = logging.getLogger(__name__)


class NewBranch(Exception): pass

def _get_git_state(odoo_repo):
        
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
                

def _make_new_instances(repo):
    while True:
        try:

            new_commits = db.git_commits.find({'triggered_update': False})
            new_branches = set([x['branch'] for x in new_commits])
            for new_branch in new_branches:
                with BUILDING_LOCK:
                    existing_site = db.sites.find_one({'name': new_branch})
                    data = {
                        'name': new_branch,
                        'needs_build': True,
                        'build_mode': 'update-recent',
                    }
                    if not existing_site:
                        data['date_registered'] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                        if _get_config('auto_create_new_branches', default=False):
                            data['build_mode'] = 'reset'
                        else:
                            # If switching auto create new branches prevent that 1000s branches are built
                            data['archive'] = True
                    else:
                        if existing_site.get('is_building') or existing_site.get('archive'):
                            continue

                    db.sites.update_one({
                        'name': new_branch,
                    }, {'$set': data}, upsert=True)
                    db.git_commits.update_many(
                        {'branch': new_branch},
                        {'$set': {'triggered_update': True}})

        except Exception as ex:
            msg = traceback.format_exc()
            logger.error(msg)

        finally:
            time.sleep(5)
