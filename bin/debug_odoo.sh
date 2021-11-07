#!/bin/bash
docker rm -f $(docker ps -f name=adminodoo -a -q)
# docker-compose build cicd_delegator
docker-compose up -d adminpostgres
cd odoo_admin
./odoo.sh reload
./odoo.sh -f debug odoo_debug -p