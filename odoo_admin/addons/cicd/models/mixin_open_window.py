from odoo import models


class OpenWindowMixin(models.AbstractModel):
    _name = 'cicd.open.window.mixin'

    def open_window(self):
        self.ensure_one()
        return {
            'view_type': 'form',
            'res_model': self._name,
            'res_id': self.id,
            'views': [(False, 'form')],
            'type': 'ir.actions.act_window',
            'target': 'current',
        }
