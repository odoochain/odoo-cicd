#!/usr/bin/env sh
set -eu

envsubst '${PROXY_PASS_INDEX}' < /etc/nginx/conf.d/default.conf.template > /etc/nginx/conf.d/default.conf

exec "$@"