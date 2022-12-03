import re
from contextlib import contextmanager
import os
import psycopg2
from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError
from pathlib import Path
from contextlib import contextmanager, closing
from odoo import registry


def lstrip(x, y):
    if x.startswith(y):
        x = x[len(y) :]
    return x


class Database(models.Model):
    _inherit = ["cicd.mixin.size"]
    _name = "cicd.database"
    _rec_name = "display_name"

    name = fields.Char("Name", required=True)
    display_name = fields.Char("Name", compute="_compute_display_name", store=False)
    server_id = fields.Many2one("cicd.postgres", string="Postgres", required=True)
    machine_id = fields.Many2one("cicd.machine", compute="_compute_machine")
    matching_branch_ids = fields.Many2many(
        "cicd.git.branch", string="Matching Branches", compute="_compute_branches"
    )
    show_revive = fields.Boolean(compute="_compute_show_revive")

    _sql_constraints = [
        (
            "name_postgres_unique",
            "unique(name, server_id)",
            _("Only one unique entry allowed."),
        ),
    ]

    def _compute_show_revive(self):
        from .postgres_server import PREFIX_TODELETE

        for rec in self:
            rec.show_revive = PREFIX_TODELETE in rec.name

    @api.depends("name", "size_human")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f"{rec.name} [{rec.size_human}]"

    @contextmanager
    def _dropconnections(self):
        self.ensure_one()
        with self.server_id._get_conn() as cr:
            cr.execute(
                (
                    f"UPDATE pg_database SET "
                    f"datallowconn = 'false' WHERE datname = '{self.name}'; \n"
                    f"SELECT pg_terminate_backend(pid) "
                    f"FROM pg_stat_activity WHERE datname = '{self.name}'; \n"
                )
            )
            cr.connection.autocommit = True
            yield cr

    def rename(self, newname):
        self.ensure_one()
        for rec in self:
            with rec._dropconnections() as cr:
                cr.execute((f"ALTER DATABASE {rec.name} RENAME TO {newname}"))
            rec.sudo().name = newname

    def delete_db(self):
        for rec in self:
            with rec._dropconnections() as cr:
                cr.execute((f"DROP DATABASE IF EXISTS {rec.name}"))
            rec.sudo().unlink()

    def revive(self):
        breakpoint()
        from .postgres_server import PREFIX_TODELETE

        for rec in self:
            with rec._dropconnections() as cr:
                original_name = rec.name.strip(PREFIX_TODELETE)
                # remove the _20220101
                original_name = "".join(
                    reversed(("".join(reversed(original_name))).split("_", 1)[1])
                )
                cr.execute((f"ALTER DATABASE {rec.name} RENAME TO {original_name}"))
                rec.sudo().name = original_name

    @api.model
    def _cron_update(self):
        for machine in self.env["cicd.machine"].sudo().search([]):
            self.env["base"].flush()
            self.env.cr.commit()

            machine._update_dumps(machine)

    def _compute_machine(self):
        for rec in self:
            machines = self.env["cicd.machine"].search(
                [("postgres_server_id", "=", rec.server_id.id)]
            )
            rec.machine_id = machines[0] if machines else False

    def _compute_branches(self):
        breakpoint()
        for rec in self:
            breakpoint()
            project_name = os.getenv("PROJECT_NAME", "")
            rec.matching_branch_ids = self.env["cicd.git.branch"]
            for repo in self.env["cicd.git.repo"].search([]):
                name = rec.name.lower()
                name = lstrip(name, project_name.lower().replace("-", "_"))
                name = name.lstrip("_")
                name = lstrip(name, repo.short.lower().replace("-", "_"))
                if not name:
                    continue
                for branch in repo.branch_ids:
                    if branch.name == name or branch.technical_branch_name == name:
                        rec.matching_branch_ids = [[6, 0, branch.ids]]
