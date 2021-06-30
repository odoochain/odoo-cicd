#!/bin/bash

export COMPOSE_HTTP_TIMEOUT=1200
docker-compose build
docker-compose down
docker-compose up -d
