from odoo import _, api, fields, models, SUPERUSER_ID
from odoo.exceptions import UserError, RedirectWarning, ValidationError


class Registry(models.Model):
    _name = 'cicd.registry'

    name = fields.Char("Name")
    host = fields.Char("Host", required=True)
    port = fields.Integer("Port", default=5000, required=True)
    username = fields.Char("Username")
    password = fields.Char("Password")
    path = fields.Char("Path", default="/myspace1", required=True)
    hub_url = fields.Char("HUB Url", compute="_compute_hub_url")

    @api.constrains("path")
    def _check_path(self):
        for rec in self:
            path = rec.path.strip()
            if not path.startswith("/"):
                path = "/" + path
            while path.endswith("/") and len(path) > 1:
                path = rec.path[:-1]

            if path != rec.path:
                rec.path = path

    def _check_passwordusername(self):
        for rec in self:
            for c in ":@":
                if c in (rec.password or '') + (rec.username or ''):
                    raise ValidationError((
                        f"Invalid Char: {c}"
                    ))

    @api.depends(
        "username",
        "password",
        "host",
        "port",
        "path",
    )
    def _compute_hub_url(self):
        for rec in self:
            rec.hub_url = (
                f"{rec.username}:{rec.password}@{rec.host}:{rec.port}{rec.path}"
            )
