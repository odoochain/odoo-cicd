import logging
import arrow
from copy import deepcopy
from datetime import datetime
import time
from .tools import _get_main_repo
from .tools import _get_config

logger = logging.getLogger(__name__)


class NewBranch(Exception): pass

def _get_new_commits(odoo_repo):
    odoo_repo._lock_git()
        
    repo = _get_main_repo(odoo_repo)

    for remote in repo.remotes:
        with odoo_repo._get_ssh_command() as env:
            fetch_info = remote.fetch(env=env)
            for fi in fetch_info:
                name = fi.ref.name.split("/")[-1]
                for skip in (odoo_repo.skip_paths or '').split(","):
                    if skip in fi.ref.name: # e.g. '/release/'
                        continue
                sha = fi.commit

                import pudb;pudb.set_trace()
                if not (branch := odoo_repo.branch_ids.filtered(lambda x: x.name == name)):
                    branch = odoo_repo.branch_ids.create({
                        'name': name,
                        'date_registered': arrow.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                        'repo_id': odoo_repo.id,
                    })

                new_commit = False
                if not (commit := branch.commit_ids.filtered(lambda x: x.name == name)):
                    new_commit = True
                    commit = branch.commit_ids.create({
                        'name': name,
                        'date_registered': arrow.utcnow().strftime("%Y-%m-%d %H:M:%S"),
                        'branch_id': branch.id,
                    })

                if new_commit:
                    try:
                        repo.git.checkout(name, force=True)
                        repo.git.pull()
                    except Exception as ex:
                        logger.error(ex)