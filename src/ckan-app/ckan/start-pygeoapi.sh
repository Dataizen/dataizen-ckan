#!/bin/bash
export MPLCONFIGDIR=/tmp/matplotlib-cache
export FONTCONFIG_CACHE=/tmp/fontconfig-cache
cd /srv/app/pygeoapi && PYGEOAPI_CONFIG=local.config.yml PYGEOAPI_OPENAPI=/srv/app/pygeoapi/openapi.yml pygeoapi serve --flask