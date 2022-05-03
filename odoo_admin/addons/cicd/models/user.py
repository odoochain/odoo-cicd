from odoo import _, api, fields, models, SUPERUSER_ID, tools
from odoo.exceptions import UserError, RedirectWarning, ValidationError


class User(models.Model):
    _inherit = 'res.users'

    debug_mode_in_instances = fields.Boolean("Debug Mode in odoo instances")

    @api.model
    def smart_find(self, a_username):
        """
        Tries to find in an intelligent way a user
        a_username may come from git or jira or so.
        """
        if not a_username:
            return False

        if '@' in a_username:
            email = a_username
            a_username = a_username.split("@")[0]

            mails = tools.email_split(email)
            for mail in mails:
                user = self.env['res.users'].search([('partner_id.email', '=', mail)])
                if user:
                    return user[0]

        for c in " .;:-_":
            a_username = a_username.replace(c, ".")

        users = self.env['res.users'].sudo().search([])
        parts = a_username.lower().split(".")
        for user in self.env['res.users'].search([]):
            if all(x in user.name.lower() for x in parts):
                return user