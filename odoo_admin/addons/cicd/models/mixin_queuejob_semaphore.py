from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT as DTF
from odoo.exceptions import UserError, RedirectWarning, ValidationError
import arrow
from contextlib import contextmanager, closing

class SemaphoreQueuejob(models.AbstractModel):
    _name = 'mixin.queuejob.semaphore'

    def _semaphore_get_queuejob(self, idkey=None):
        self.ensure_one()
        idkey = idkey or self.semaphore_qj_identity_key
        return self.env['queue.job'].sudo().search([(
                'identity_key', '=', idkey)], limit=1)

    @property
    def semaphore_qj_identity_key(self):
        return (
            f"{self._name},"
            f"{self.id}"
        )

    @contextmanager
    def qj_semaphore(self, enabled=True):
        if not enabled:
            yield

        else:
            self.env.cr.execute((
                "select count(*) "
                "from queue_job "
                "where identity_key = %s"
            ), tuple([self.qj_identity_key]))
            count_jobs = self.env.cr.fetchone()[0]
            if not count_jobs:
                yield

    def semaphore_with_delay(self, enabled, appendix=False, **params):
        _params = {}
        _params.update(params)
        params['identity_key'] = self.semaphore_qj_identity_key
        if appendix:
            params['identity_key'] += appendix

        jobs = self._semaphore_get_queuejob(params['identity_key'])
        if not enabled:
            yield self
        else:
            if jobs and jobs.state in ['done', 'failed']:
                yield self.with_delay(**params)