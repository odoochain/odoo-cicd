import arrow
from odoo import http
from odoo.http import request

class Controller(http.Controller):

    @http.route("/last_access/<name>", type="json")
    def last_access(name):
        branch = request.env['cicd.git.branch'].sudo().search([('name', '=', name)])
        branch.last_access = arrow.utcnow().datetime
        return {'result': 'ok'}

    @http.route("/start/<name>")
    def start_instance(name):
        branch = request.env['cicd.git.branch'].sudo().search([('name', '=', name)])

        redirect = request.redirect("/web/login")
        redirect.set_cookie('delegator-path', name)
        redirect.set_cookie('frontend_lang', '', expires=0)
        redirect.set_cookie('im_livechat_history', '', expires=0)
        redirect.set_cookie('session_id', "", expires=0)
        return redirect