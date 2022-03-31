from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT as DTF
from odoo.exceptions import UserError, RedirectWarning, ValidationError
import arrow
from contextlib import contextmanager, closing

class SemaphoreQueuejob(models.AbstractModel):
    _name = 'mixin.queuejob.semaphore'

    def _semaphore_get_queuejob(self, idkey=None, limit=None):
        self.ensure_one()
        idkey = idkey or self.semaphore_qj_identity_key
        return self.env['queue.job'].sudo().search([(
                'identity_key', '=', idkey)
                ], order='id desc', limit=limit)

    @property
    def semaphore_qj_identity_key(self):
        return (
            f"{self._name},"
            f"{self.id}"
        )


    @contextmanager
    def semaphore_with_delay(self, enabled, appendix=False, ignore_states=None, **params):
        _params = {}
        _params.update(params)

        if not enabled:
            yield self
        else:
            ignore_states = tuple(ignore_states or [])
            params['identity_key'] = self.semaphore_qj_identity_key
            if appendix:
                params['identity_key'] += appendix

            jobs = self._semaphore_get_queuejob(params['identity_key'])
            if ignore_states:
                jobs = jobs.filtered(lambda x: x.state not in ignore_states)

            if not jobs or all(x.state in ['done'] for x in jobs):
                new_self = self.with_delay(**params)
                yield new_self
            else:
                yield None