from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT as DTF
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from croniter import croniter


class CicdSchedule(models.Model):
    _name = 'cicd.schedule'

    name = fields.Char('Name', required=True)
    schedule = fields.Char('Schedule', required=True, help='Crontab based schedule:\n*/5 * * * * => every 5 minutes\n* * * * * */10 => every 10 seconds')
    
    @api.model
    def _get_next(self, schedule, start_from=None):
        start_from = start_from or fields.Datetime.now()
        return croniter(schedule, start_from).get_next(type(start_from))

    def _compute_next(self, start_from=None):
        self.ensure_one()
        return self._get_next(self.schedule, start_from)

    def compute_next(self, start_from=None):
        start_from = start_from or fields.Datetime.now()
        dates = [rec._compute_next(start_from) for rec in self]
        return dates

    def compute_next_min(self, start_from=None):
        dates = self.compute_next(start_from)
        return dates and min(dates)

    def compute_next_max(self, start_from=None):
        dates = self.compute_next(start_from)
        return dates and max(dates)


class Schedule(models.AbstractModel):
    _name = 'mixin.schedule'

    schedule_line_ids = fields.Many2many('cicd.schedule', string='Schedules')

    def _compute_next_date(self, start_from=None):
        return self.schedule_line_ids.compute_next_min(start_from)

    def compute_next_date(self, start_from=None):
        return fields.Datetime.to_string(
            self._compute_next_date(start_from)
        )
    
