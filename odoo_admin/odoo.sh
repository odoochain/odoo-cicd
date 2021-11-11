#!/bin/bash
set -e
SCRIPT_DIR="$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd $SCRIPT_DIR
export $( cat "$SCRIPT_DIR/../.env" | xargs)

RESTRICT="--restrict-docker-compose ""${SCRIPT_DIR}/docker-compose.yml"" --restrict-setting ""${SCRIPT_DIR}/../.env"" --restrict-setting ""${SCRIPT_DIR}/settings"" --project-name ""${CICD_NETWORK_NAME}adminodoo"" "

odoo $RESTRICT "$@"

