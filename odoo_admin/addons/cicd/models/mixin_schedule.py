from odoo import fields, models


class Schedule(models.AbstractModel):
    _name = "mixin.schedule"

    schedule_line_ids = fields.Many2many("cicd.schedule", string="Schedules")

    def _compute_next_date(self, start_from=None):
        return self.schedule_line_ids.compute_next_min(start_from)

    def compute_next_date(self, start_from=None):
        return fields.Datetime.to_string(self._compute_next_date(start_from))
