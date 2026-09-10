#!/bin/bash
# Fix CKAN site URL to use localhost:8080 instead of localhost:5000

#echo "Fixing CKAN site URL..."

# Wait for CKAN to be ready
#sleep 5

# Fix the site URL in ckan.ini
#if [ -f "/srv/app/ckan.ini" ]; then
 #   sed -i 's|ckan\.site_url = http://localhost:5000|ckan.site_url = http://localhost:8080|' /srv/app/ckan.ini
  #  echo "CKAN site URL fixed to http://localhost:8080"
#else
 #   echo "ckan.ini not found, skipping URL fix"
#fi

