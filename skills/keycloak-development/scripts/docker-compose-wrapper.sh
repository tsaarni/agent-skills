#!/bin/sh -ex

cd $HOME/work/devenvs/keycloak
docker compose "$@"
