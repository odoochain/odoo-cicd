import random
from contextlib import contextmanager
from pathlib import Path
import tempfile
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from ..tools import lib_git_fetch
from . import pg_try_advisory_lock
from odoo.addons.queue_job.exception import (
    RetryableJobError,
    JobError,
)
class Repository(models.Model):
    _name = 'cicd.git.repo'

    name = fields.Char("URL", required=True)
    login_type = fields.Selection([
        ('username', 'Username'),
        ('key', 'Key'),
    ])
    key = fields.Text("Key")
    username = fields.Char("Username")
    password = fields.Char("Password")
    skip_paths = fields.Char("/release/", help="Comma separated list")
    branch_ids = fields.One2many('cicd.git.branch', 'repo_id', string="Branches")
    url = fields.Char(compute="_compute_url")

    _sql_constraints = [
        ('name_unique', "unique(named)", _("Only one unique entry allowed.")),
    ]

    def _compute_url(self):
        import pudb;pudb.set_trace()
        for rec in self:
            if rec.login_type == 'username':
                if rec.name.startswith('https:'):
                    rec = f'https://{rec.username}:{rec.password}@{rec.name[8]}'
                else:
                    raise NotImplementedError(rec.name)
                rec.url = url
            else:
                rec.url = rec.name

    @contextmanager
    def _get_ssh_command(self):
        self.ensure_one()
        file = Path(tempfile.mktemp(suffix='.'))
        env = {}

        try:
            if self.login_type == 'key':
                env['GIT_SSH_COMMAND'] = f'ssh -i {file}'
                file.write_text(self.key)
            else:
                pass
            yield env
        finally:
            file.unlink()


    @api.model
    def _cron_fetch(self):
        for repo in self.search([]):
            lib_git_fetch._get_new_commits(repo)

    def _lock_git(self): 

        def retry(lock):
            raise RetryableJobError(f'Could not acquire advisory lock (stock move line {lock})', seconds=random.randint(5, 15), ignore_retry=True)

        for rec in self:
            lock = rec.name
            if not pg_try_advisory_lock(self.env.cr, lock):
                retry(lock)