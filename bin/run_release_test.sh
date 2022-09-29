#!/bin/bash
set -ex
./cicd down -v
./cicd up -d postgres
./cicd -f db reset
./cicd update
./cicd up -d
./cicd turn-into-dev
./cicd robot tests/test_release.robot \
	--param CICD_HOME=$( pwd ) \
	--param CICDRELEASE_ODOOCMD=/home/cicdrelease/.local/bin/odoo \
	--param ROBOTTEST_SSH_USER=$USER