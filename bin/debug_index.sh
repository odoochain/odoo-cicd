#!/bin/bash
# doker-compose down
docker-compose build cicd_index
docker-compose rm -f webssh
docker-compose build webssh
docker-compose up -d webssh
docker rm -f $(docker ps -f name=cicd_index -a -q)
#docker-compose run --name cicd_index --rm --service-ports cicd_index waitress-serve --port=5000 --call app1:create_app
echo For debugging use now
echo hupper -v -w /app1 -m waitress --port=5000 --call app1:create_app
docker-compose run --name cicd_index --rm --service-ports cicd_index bash 
