import base64
from pathlib import Path
import subprocess
import tempfile
import arrow
from odoo import http
from odoo.http import content_disposition, request
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT as DTF
import logging
logger = logging.getLogger(__name__)


class Controller(http.Controller):

    @http.route("/last_access/<name>", type="http", auth="public")
    def last_access(self, name):
        branch = request.env['cicd.git.branch'].sudo().search([(
            'project_name', '=', name)])
        branch.last_access = arrow.utcnow().datetime.strftime(DTF)
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
        logger.info(f"Starting branch {name}")
        action = args.get('action')
        # branch = request.env['cicd.git.branch'].sudo().search([
        #     ('name', '=', name)], limit=1).with_context(prefetch_fields=False)
        # TODO improve performance
        branch = request.env['cicd.git.branch'].sudo().search([
        ]).with_context(prefetch_fields=False).filtered(
            lambda x: x.project_name == name)
        request.env.cr.commit()
        if not branch:
            return (
                f"Did not find {name}."
            )
        branch = branch[0]

        # first try to get login page, if this not success then try to start
        # containers
        try:
            branch.make_instance_ready_to_login()
        except Exception as ex:
            logger.error(str(ex))
            return (
                "Unable to login - could not start instance.<br/>"
                f"{str(ex)}"
            )

        url = "/web/login"
        if request.env.user.debug_mode_in_instances:
            url += "?debug=1"

        redirect = request.redirect(
            url if not action else "/" + action + "/")  # e.g. mailer/
        expires = arrow.utcnow().shift(hours=2)  # .strftime("%a, %d %b %Y %H:%M:%S GMT")
        redirect.set_cookie('delegator-path', name, expires=expires.datetime)
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
            dump.machine_id.sudo().message_post(
                body="Downloaded dump: " + dump.name)

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
            url = '/web'
        redirect = request.redirect(url)
        return redirect

    @http.route("/trigger/repo/<webhook_id>/<webhook_secret>", auth='public', type="json")
    def _trigger_repo_update(self, webhook_id, webhook_secret, **kwargs):
        repos = request.env['cicd.git.repo'].sudo().search([
            ('webhook_id', '=', webhook_id),
            ('webhook_secret', '=', webhook_secret),
        ]).with_context(prefetch_fields=False)
        if not repos:
            raise Exception("Invalid webhook")
        request.env.cr.commit()
        for repo in repos:
            # no identity key, why:
            # 2 quick push events shall trigger a fetch, otherwise in very rare conditions
            # the event could be lost
            repo.with_delay(identity_key=(
                f"queuejob-fetch-{repo.short}"
            ))._queuejob_fetch()
        return {"result": "ok"}

    @http.route([
        "/robot_output/<model('cicd.test.run.line'):line>",
        ])
    def robot_output(self, line, **kwargs):
        line = line.sudo()
        if not line.robot_output:
            return 'no data'

        path = Path(f"/tmp/robot_output/{request.env.cr.dbname}/{line.id}")
        path.mkdir(exist_ok=True, parents=True)

        filename = Path(tempfile.mktemp())
        try:
            content = base64.b64decode(line.robot_output)
            filename.write_bytes(content)

            subprocess.check_call([
                "tar", "xfz", filename
            ], cwd=path)

            html = list(path.glob("**/log.html"))
            if html:
                html = html[0].read_text()
                html = html.replace("src=\\\"", f"src=\\\"{line.id}/")
                html = html.replace("href=\\\"", f"href=\\\"{line.id}/")
                return html

        finally:
            if filename.exists():
                filename.unlink()

    @http.route([
        "/robot_output/<model('cicd.test.run.line'):line>/<filepath>",
        ])
    def robot_output_resource(self, line, filepath, **kwargs):
        line = line.sudo()
        path = Path(f"/tmp/robot_output/{request.env.cr.dbname}/{line.id}")

        filepath = ''.join(reversed(''.join(reversed(filepath)).split("/", 1)[0]))
        filepath = list(path.glob("**/" + filepath))
        filename, content = None, None
        for filepath in filepath:
            filepath = Path(filepath)
            if filepath.exists():
                content = filepath.read_bytes()
                filename = filepath.name

        if content:
            return http.request.make_response(content, [
                ('Content-Type', 'image/png'),
            ])

