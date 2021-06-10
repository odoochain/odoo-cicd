#!/bin/bash
# doker-compose down
docker-compose build cicd_cronjobs
docker-compose rm -f webssh
docker-compose build webssh
docker-compose up -d webssh
docker rm -f $(docker ps -f name=cicd_cronjobs -a -q)
#docker-compose run --name cicd_cronjobs --rm --service-ports cicd_cronjobs python3 -m app1

#echo hupper -v -w /app1 -m waitress --port=5000 --call app1:create_app
docker-compose run --name cicd_cronjobs --rm --service-ports cicd_cronjobs hupper python3 -m app1