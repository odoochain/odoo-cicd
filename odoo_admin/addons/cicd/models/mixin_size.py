import humanize
from odoo import api, fields, models


class MixinSize(models.AbstractModel):
    _name = 'cicd.mixin.size'

    size = fields.Float("Size")
    size_human = fields.Char("Size", compute="_humanize")

    @api.depends('size')
    def _humanize(self):
        for rec in self:
            rec.size_human = humanize.naturalsize(rec.size)


class MixinSizeDocker(models.AbstractModel):
    _name = 'cicd.mixin.docker'
