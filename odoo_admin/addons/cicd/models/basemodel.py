# !!!!!! ATTENTION IF USED IN NORMAL ODOO PROJECT BELOW !!!!!!!!
from odoo import _, api, models, SUPERUSER_ID
from contextlib import contextmanager, closing


class Base(models.AbstractModel):
    _inherit = 'base'

    @contextmanager
    def _extra_env(self, obj=None, enabled=True):
        obj = obj or self
        if not enabled:
            yield obj
        else:

            # avoid long locking
            with closing(self.env.registry.cursor()) as cr:
                env = api.Environment(cr, SUPERUSER_ID, self._context)
                env.reset()
                obj = obj.with_env(env).with_context(prefetch_fields=False)

                try:
                    yield obj

                finally:
                    env.cr.rollback()
                    env.clear()

    def _unblocked_read(self, fields):
        self.ensure_one()
        with self._extra_env() as self:
            res = self.read(fields)[0]
        return res

    def _unblocked(self, field):
        self.ensure_one()
        return self._unblocked_read([field])[field]

    # !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
    # CAREFUL! If you integrate this class in your project
    # then remove following defs!!!!!!!!!!!!!!!!!!!!!!!!!!!
    def read(self, *args, **kwargs):
        self = self.with_context(prefetch_fields=False)
        return super().read(*args, **kwargs)

    def browse(self, *args, **kwargs):
        self = self.with_context(prefetch_fields=False)
        return super().browse(*args, **kwargs)

