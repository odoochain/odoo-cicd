import arrow
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT as DTF


class Query(models.TransientModel):
    _name = 'db.query'

    pid = fields.Integer("PID")
    state = fields.Char("State")
    started = fields.Datetime("Started")
    usename = fields.Char("Username")
    name = fields.Char("Query")
    age = fields.Float("Age Seconds (Total)", compute_sudo=True, store=False)
    age_minutes = fields.Float(
        "Age Minutes (Total)", compute="_compute_age", compute_sudo=True)
    age_hours = fields.Float(
        "Age Hours (Total)", compute="_compute_age", compute_sudo=True)

    @api.depends('started')
    def _compute_age(self):
        for rec in self:
            if rec.started:
                rec.age = (
                    arrow.utcnow() - arrow.get(rec.started)).total_seconds()
            else:
                rec.age = 0
            rec.age_minutes = rec.age / 60.0
            rec.age_hours = rec.age / 3600.0

    def cancel(self):
        self.env.cr.execute((
            "select pg_terminate_backend(%s)"
        ), (self.id,))
        self.sudo()._update_queries()

    @api.model
    def _update_queries(self):
        self.env.cr.execute((
            "select pid, query_start, state, query, usename "
            "from pg_stat_activity "
            "where "
            "query <> 'COMMIT' "
            "and query <> 'ROLLBACK' "
            "and query not like '%pg_stat_activity%' "
        ))
        pids = set()

        def convdt(x):
            if not x:
                return False
            else:
                return x.strftime(DTF)
        for query in self.env.cr.dictfetchall():
            query['name'] = query.pop('query')
            query['started'] = convdt(query.pop('query_start'))
            queries = self.search([('pid', '=', query['pid'])])
            if not query['name']:
                continue
            if not queries:
                queries.create(query)
            else:
                queries.write(query)
            pids.add(query['pid'])

        self.search([('pid', 'not in', list(pids))]).unlink()
