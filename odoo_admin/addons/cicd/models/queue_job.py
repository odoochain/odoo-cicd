import re
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from odoo.addons.queue_job.job import Job


class queuejob(models.Model):
    _inherit = 'queue.job'

    def run_now(self):
        for self in self:
            self.ensure_one()

            job = Job.load(self.env, self.uuid)

            job.set_started()
            job.store()
            job.perform()
            job.set_done()
            job.store()

    branch = fields.Char(compute="_compute_branch", store=False)

    def _compute_branch(self):
        for rec in self:
            b = False
            if rec.identity_key:
                b = re.findall(r'branch:([^:]*):', rec.identity_key)
                if b:
                    b = b[0]
                else:
                    b = False
            if b != rec.branch:
                rec.branch = b

    @api.model
    def requeue_jobs(self):
        reasons = [
            'could not serialize access due to concurrent update',
            'cannot stat',
            'server closed the connection unexpectedly',
            'RetryableJobError',
            'Failed to put the local file',
            'psycopg2.errors',
            '.git/index.lock',
            'duplicate key value violates unique constraint "cicd_git_commit_name"',
            # following happened when using bitbucket - was slow on that day and timed out
            'Could not read from remote repository',
            'Lock could not be acquired',
            'LockNotAvailable',
            'Permission denied',
            'does not exist yet',
        ]

        ignore_idkeys = [
            'docker-containers',
            'dump-update',
            'last-access-',
            'machine-update-vol-sizes-',
            'update_databases',
        ]

        for reason in reasons:
            for job in model.search([('state', '=', 'failed'), ('exc_info', 'ilike', reason)]):
                for ignore in ignore_idkeys:
                    if ignore in job.identity_key:
                        break
                else:
                    job.state = 'pending'