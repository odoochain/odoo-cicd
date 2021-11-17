#!/bin/bash
set -x

export COMPOSE_HTTP_TIMEOUT=1200
cd odoo_admin
./odoo.sh reload
./odoo.sh up -d
