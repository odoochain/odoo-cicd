import arrow
from odoo import http
from odoo.http import request

class Controller(http.Controller):

    @http.route("/last_access/<name>", type="http", auth="public")
    def last_access(self, name):
        branch = request.env['cicd.git.branch'].sudo().search([('name', '=', name)])
        branch.last_access = arrow.utcnow().datetime.strftime("%Y-%m-%d %H:%M:%S")
        return "OK"

    @http.route(["/start/<name>", "/start/<name>/<action>"])
    def start_instance(self, name, **args):
        action = args.get('action')
        branch = request.env['cicd.git.branch'].sudo().search([])
        branch = branch.filtered(lambda x: x.project_name == name)

        # first try to get login page, if this not success then try to start containers
        branch.make_instance_ready_to_login()

        redirect = request.redirect("/web/login" if not action else "/" + action + "/") # e.g. mailer/
        redirect.set_cookie('delegator-path', name)
        redirect.set_cookie('frontend_lang', '', expires=0)
        redirect.set_cookie('im_livechat_history', '', expires=0)
        redirect.set_cookie('session_id', "", expires=0)
        return redirect