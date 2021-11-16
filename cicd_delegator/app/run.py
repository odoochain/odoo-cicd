#!/usr/bin/env python3
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
import datetime
import argparse
import os
import sys
import requests
import logging
import traceback
from urllib import parse

cicd_index_url = os.environ['INDEX_HOST']

FORMAT = '[%(levelname)s] %(name) -12s %(asctime)s %(message)s'
logging.basicConfig(format=FORMAT)
logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger('')  # root handler
logger.info("Starting cicd delegator reverse-proxy")
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)


def ignore_case_get(dict, key):
    keys = list(dict.keys())
    lkeys = [x.lower() for x in keys]
    idx = lkeys.index(key.lower())
    if idx >= 0:
        return dict[keys[idx]]
    return None

def parse_cookies(cookie):
    """
    roundcube_sessauth=-del-; expires=Tue, 02-Mar-2021 16:36:26 GMT; Max-Age=0; path=/;
    HttpOnly, roundcube_sessid=93gt0c9a8c7njtt5f6tpa0t1h2; path=/; HttpOnly,
    roundcube_sessauth=Od9cAxp8lkWwbsjjQ8KWMNQBRW-1614702900; path=/; HttpOnly'

    SIMPLE Cookie is buggy cannot parse im_live_chat=['asd']; admin_sesseion_id=...
    """
    while ' =' in cookie:
        cookie = cookie.replace(' =', '=')
    arr = cookie.split(";")
    cookies = []

    keywords = ['expires', 'max-age', 'domain', 'path', 'httponly']

    def extract_keywords(s):
        found = []
        s = s.strip()
        if 'set-cookie:' in s.lower():
            s = s[s.lower().index('set-cookie:') + len('set-cookie:'):]
            s = s.strip()
        splitted = s.split(',')
        filtered = []
        for x in splitted:
            if x.lower().strip() in keywords and '=' not in x:
                # e.g. HttpOnly, MyCookie=123
                found.append(x)
                x = ""
            else:
                for kw in keywords:
                    if x.lower().startswith(kw + '='):
                        filtered.append(x)
                        x = ""
            if x:
                filtered.append(x)
        return found, ','.join(filtered)

    for part in arr:
        part = part.strip()

        # extract keywords and append
        append, part = extract_keywords(part)

        if '=' in part:
            if not any(part.strip().lower().startswith(x + '=') for x in keywords):
                cookies.append([])
            else:
                continue
        else:
            continue

        cookies[-1].append(part.strip())
        if append:
            cookies[-1] += append
            append = []

    cookies = dict(x[0].split("=", 1) for x in cookies)
    return cookies

class ProxyHTTPRequestHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def _merge_headers(self, *arrs):
        headers = sum(arrs)
        return headers

    def do_HEAD(self):
        self.do_GET(body=False)

    def _handle_error(self, ex):
        logger.error(ex)
        self.send_error(501, str(ex))

    def _rewrite_path(self, header, cookies):
        url = ""
        if cookies and cookies.get('delegator-path'):
            delegator_path = cookies.get('delegator-path', "")
            delegator_path = delegator_path
        else:
            delegator_path = 'not-set'
        if delegator_path == 'not-set':
            delegator_path = ""

        if delegator_path:
            # set touched date:
            requests.get(cicd_index_url + "/last_access/" + delegator_path).raise_for_status()

        path = (self.path or '').split("?")[0]
        if path in ['/index', '/index/'] or not delegator_path:
            path = self.path
            if path.split("/")[1] == 'index':
                path = '/'
            url = f'{cicd_index_url}{path}'
        elif path.startswith("/mailer/") and delegator_path:
            host = f"{delegator_path}_proxy"
            url = f'http://{host}{path}'
        elif path.startswith("/logs/") and delegator_path:
            host = f"{delegator_path}_proxy"
            url = f'http://{host}{path}'
        else:
            host = f"{delegator_path}_proxy"
            url = f'http://{host}{path}'

        logger.debug(f"rewrite path result: {url}")
        return url

    def _redirect_to_index(self):
        # do logout to odoo to be clean; but redirect to index

        content = """
        Redirecting to cicd application...
        <script>
        window.location = "/index";
        </script>
        """.encode('utf-8')

        self.send_response(200)
        null = "deleted; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT"
        self.send_header("Set-Cookie", "delegator-path=" + null)
        self.send_header("Set-Cookie", "session_id=" + null)
        self.send_header("content-type", "text/html; charset=UTF-8")
        self.send_header("content-length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self, body=True):
        sent = False

        query_params = dict(parse.parse_qsl(parse.urlsplit(self.path).query))
        try:
            req_header, cookies = self.parse_headers()
            url = self._rewrite_path(req_header, cookies)
            resp = requests.get(
                url, headers=req_header, verify=False,
                allow_redirects=False, params=query_params,
                cookies=cookies,
            )
            sent = True

            if self.path == "/_" or self.path.endswith('/web/session/logout'):
                self._redirect_to_index()
            else:
                self.send_response(resp.status_code)
                self.send_resp_headers(resp, cookies)
                if body:
                    self.wfile.write(resp.content)

            return
        except Exception as ex:
            self._handle_error(ex)
        finally:
            self.finish()
            if not sent:
                self.send_error(404, 'error trying to proxy')

    def do_POST(self, body=True):
        req_header, cookies = self.parse_headers()
        url = self._rewrite_path(req_header, cookies)
        sent = False
        try:
            content_len = int(self.headers.get('content-length', 0))
            post_body = self.rfile.read(content_len)

            resp = requests.post(
                url, data=post_body,  headers=req_header,
                verify=False, allow_redirects=False,
                cookies=cookies,
            )
            sent = True

            self.send_response(resp.status_code)
            self.send_resp_headers(resp, cookies)
            if body:
                self.wfile.write(resp.content)
            return
        except Exception as ex:
            msg = traceback.format_exc()
            logger.error(msg)
        finally:
            self.finish()
            if not sent:
                self.send_error(404, 'error trying to proxy')

    def parse_headers(self):
        req_header = {}
        for line in self.headers.as_string().split("\n"):
            if not line:
                continue
            line_parts = [o.strip() for o in line.split(':', 1)]
            if len(line_parts) == 2:
                key = line_parts[0]
                if key.lower() == 'cookie':
                    key = 'Cookie'
                req_header[key] = line_parts[1]

        cookies = {}
        if req_header.get('Cookie'):
            cookies = parse_cookies(req_header['Cookie'])

        return req_header, cookies

    def _set_cookies(self, cookie):
        logger.debug(f"Path is: {self.path}")

        if '/__start_cicd' in self.path:
            site = self.path.split("/")[1]
            cookie['delegator-path'] = site
            cookie['delegator-path']['max-age'] = 365 * 24 * 3600
            cookie['delegator-path']['path'] = '/'
        elif self.path in ['/index', '/index/'] or self.path.endswith('/web/session/logout'):
            cookie['delegator-path'] = "not-set"
            cookie['delegator-path']['path'] = '/'

    def send_resp_headers(self, resp, cookies):
        self._set_cookies(cookies)

        respheaders = resp.headers
        logger.debug('Response Header')
        for key in respheaders:
            if (key or '').lower() not in [
                'content-encoding', 'transfer-encoding', 'content-length',
                'set-cookie',
            ]:
                self.send_header(key, respheaders[key])
        self.send_header('Content-Length', len(resp.content))

        cookies_dict = {}
        for key, morsel in cookies.items():
            cookies_dict[key] = morsel
        if resp.headers.get('set-cookie'):
            for key, morsel in parse_cookies(resp.headers['set-cookie']).items():
                cookies_dict[key] = morsel

        def set_cookie_value_item(cookie_value, item, value):
            cookie_value = cookie_value.split(";")
            cookie_value = [x for x in cookie_value if item.lower() + "=" not in x.lower()]
            cookie_value.append(f"{item}={value}")
            return "; ".join(cookie_value)

        for key, value in cookies_dict.items():
            value = set_cookie_value_item(value, "path", "/")
            value = set_cookie_value_item(value, "Expires", (datetime.datetime.utcnow() + datetime.timedelta(days=30)).strftime("%a, %d %b %Y %H:%M:%S GMT"))
            self.send_header('Set-Cookie', f"{key}={value}")

        self.end_headers()


def parse_args(argv=sys.argv[1:]):
    parser = argparse.ArgumentParser(description='Proxy HTTP requests')
    parser.add_argument(
        '--port', dest='port', type=int,
        default=80, help='serve HTTP requests on specified port (default: 80)'
    )
    args = parser.parse_args(argv)
    return args

def main(argv=sys.argv[1:]):
    args = parse_args(argv)
    logger.info('http server is starting on port {}...'.format(args.port))
    server_address = ('0.0.0.0', args.port)
    httpd = ThreadingHTTPServer(server_address, ProxyHTTPRequestHandler)
    logger.info('http server is running as reverse proxy')
    logger.info(f"Starting reverse proxy on {server_address}")
    httpd.serve_forever()


if __name__ == '__main__':
    main()
