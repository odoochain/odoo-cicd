from odoo import _, api, fields, models, SUPERUSER_ID, tools
from contextlib import contextmanager, closing


class MixinExtraEnv(models.AbstractModel):
    _name = 'cicd.mixin.extra_env'

    @contextmanager
    def _extra_env(self, obj=None):
        obj = obj or self
        obj.ensure_one()

        # avoid long locking
        with closing(self.env.registry.cursor()) as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            obj = obj.with_env(env)

            try:
                yield obj

            finally:
                env.cr.rollback()
                env.clear()

    def _unblocked_read(self, fields):
        with self._extra_env() as branch:
            res = {}
            for field in fields:
                res[field] = branch[field]
        return res
