#!/bin/bash
# To allow execution of "odoo" via ssh:
# * change sshd_config PermitUserEnvironment yes
# * add /home/<user>/.ssh/environment with:
#   PATH=/usr/local/sbin:/usr/local/bin:/usr/bin:/home/cicdrelease/.local/sudobin:/home/cicdrelease/.local/bin

# One time prep:
# copy git-cicd to release user ~/.local/bin

set -ex
./cicd down -v
./cicd up -d postgres
./cicd -f db reset
./cicd update
./cicd up -d
./cicd turn-into-dev
./cicd robot tests/test_release.robot \
	--param CICD_HOME=$( pwd ) \
	--param ROBOTTEST_SSH_USER=$USER