#!/bin/bash

export COMPOSE_HTTP_TIMEOUT=1200
docker-compose build
docker-compose down
cd odoo_admin
./odoo.sh reload
./odoo.sh up -d
docker-compose up -d
