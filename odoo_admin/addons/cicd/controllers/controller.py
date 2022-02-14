import base64
import arrow
from odoo import http
from odoo.http import content_disposition, request

class Controller(http.Controller):

    @http.route("/last_access/<name>", type="http", auth="public")
    def last_access(self, name):
        branch = request.env['cicd.git.branch'].sudo().search([('project_name', '=', name)])
        branch.last_access = arrow.utcnow().datetime.strftime("%Y-%m-%d %H:%M:%S")
        return "OK"

    @http.route('/start/<name>/mailer/startup')
    def _start_mailer(self, name, **kwargs):
        """
        """
        return """

        <html>
        <head>
        </head>
        <script type='text/javascript'>
        window.open('/start/{name}/mailer/');
        window.location.href = '/start/{name}';
        </script>
        <body>
        Opening Mailer in popup and odoo login
        </body>
        """.format(name=name)

    @http.route(["/start/<name>", "/start/<name>/<action>"])
    def start_instance(self, name, **args):
        action = args.get('action')
        branch = request.env['cicd.git.branch'].sudo().search([])
        branch = branch.filtered(lambda x: x.project_name == name)

        # first try to get login page, if this not success then try to start containers
        branch.make_instance_ready_to_login()

        url = "/web/login"
        if request.env.user.debug_mode_in_instances:
            url += "?debug=1"

        redirect = request.redirect(url if not action else "/" + action + "/") # e.g. mailer/
        redirect.set_cookie('delegator-path', name)
        redirect.set_cookie('frontend_lang', '', expires=0)
        redirect.set_cookie('im_livechat_history', '', expires=0)
        redirect.set_cookie('session_id', "", expires=0)
        return redirect

    @http.route(["/download/dump/<model('cicd.dump'):dump>"])
    def download_dump(self, dump, **args):
        if not request.env.user.has_group("cicd.group_download_dumps"):
            return "Forbidden"

        with dump.machine_id._shell(cwd='~', logsio=None) as shell:
            content = shell.get(dump.name)
            dump.machine_id.sudo().message_post(body="Downloaded dump: " + dump.name)

        name = dump.name.split("/")[-1]

        return http.request.make_response(content, [
            ('Content-Type', 'application/octet-stream; charset=binary'),
            ('Content-Disposition', content_disposition(name))
        ])

    @http.route('/redirect_from_instance')
    def _redirect_from_instance(self, instance, **kwargs):
        """
        On logout of the instance this url is called and user is redirect to branch.
        """
        branch = request.env['cicd.git.branch'].sudo().search([])
        branch = branch.filtered(lambda x: x.project_name.lower() == instance.lower())
        if branch:
            menu_id = request.env.ref("cicd.root_menu").id
            url = f"/web#menu_id={menu_id}&model=cicd.git.branch&id={branch and branch.id or 0}&view_type=form"
        else:
            url = f'/web'
        redirect = request.redirect(url)
        return redirect
