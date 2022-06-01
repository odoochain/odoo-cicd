from odoo import fields, models, api
from croniter import croniter
from cron_descriptor import get_description


class CicdSchedule(models.Model):
    _name = 'cicd.schedule'

    name = fields.Char('Name', required=True)
    schedule = fields.Char(
        'Schedule', required=True,
        help=(
            'Crontab based schedule:\n'
            '*/5 * * * * => every 5 minutes\n'
            '* * * * * */10 => every 10 seconds')
    )
    human_name = fields.Char(compute="_compute_human")

    @api.depends('schedule')
    def _compute_human(self):
        for rec in self:
            text = False
            if rec.schedule:
                text = get_description(rec.schedule)
            rec.human_name = text

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
