FROM ubuntu:22.04

# Éviter les prompts interactifs
ENV DEBIAN_FRONTEND=noninteractive

# Installer MapServer, Apache et les dépendances
# Note: mapserver-bin et mapserver-dev peuvent ne pas être disponibles dans tous les dépôts
# Si nécessaire, installer depuis les sources ou utiliser un PPA
RUN apt-get update && apt-get install -y \
    apache2 \
    python3 \
    python3-pip \
    python3-requests \
    postgresql-client \
    libpq-dev \
    python3-psycopg2 \
    gdal-bin \
    postgis \
    libproj-dev \
    libgdal-dev \
    libgeos-dev \
    libcurl4-openssl-dev \
    libxml2-dev \
    libfreetype6-dev \
    libjpeg-dev \
    libpng-dev \
    && pip3 install psycopg2-binary \
    && rm -rf /var/lib/apt/lists/*

# Installer MapServer depuis les packages Ubuntu
# Le package cgi-mapserver installe mapserv dans /usr/lib/cgi-bin/mapserv
# Si les packages ne sont pas disponibles, voir INSTALLATION.md pour des alternatives
RUN apt-get update && ( \
    apt-get install -y cgi-mapserver mapserver-bin 2>/dev/null || \
    (echo "⚠️ Packages MapServer non disponibles dans les dépôts par défaut" && \
     echo "   Ajout du PPA UbuntuGIS..." && \
     apt-get install -y software-properties-common && \
     add-apt-repository -y ppa:ubuntugis/ubuntugis-unstable && \
     apt-get update && \
     apt-get install -y cgi-mapserver mapserver-bin) \
) && rm -rf /var/lib/apt/lists/*

# Vérifier que mapserv est disponible et créer un lien si nécessaire
RUN if [ -f /usr/lib/cgi-bin/mapserv ]; then \
        echo "✅ mapserv trouvé dans /usr/lib/cgi-bin/mapserv"; \
    elif [ -f /usr/bin/mapserv ]; then \
        ln -s /usr/bin/mapserv /usr/lib/cgi-bin/mapserv && \
        chmod +x /usr/lib/cgi-bin/mapserv && \
        echo "✅ mapserv lié depuis /usr/bin/mapserv"; \
    else \
        MAPSERV_PATH=$(find /usr -name mapserv 2>/dev/null | head -1) && \
        if [ -n "$MAPSERV_PATH" ]; then \
            ln -s "$MAPSERV_PATH" /usr/lib/cgi-bin/mapserv && \
            chmod +x /usr/lib/cgi-bin/mapserv && \
            echo "✅ mapserv trouvé et lié: $MAPSERV_PATH"; \
        else \
            echo "❌ mapserv non trouvé - l'installation MapServer a échoué"; \
            exit 1; \
        fi; \
    fi

# Activer les modules Apache nécessaires
RUN a2enmod cgi \
    && a2enmod rewrite

# Créer les répertoires nécessaires
RUN mkdir -p /mapserver/mapfiles \
    && mkdir -p /mapserver/data \
    && mkdir -p /mapserver/logs \
    && mkdir -p /mapserver/fonts \
    && chown -R www-data:www-data /mapserver

# Créer le fichier fonts.list pour MapServer
RUN echo "Arial /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf" > /mapserver/fonts/fonts.list && \
    echo "Arial-Bold /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" >> /mapserver/fonts/fonts.list && \
    echo "Arial-Italic /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf" >> /mapserver/fonts/fonts.list && \
    echo "Courier /usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf" >> /mapserver/fonts/fonts.list && \
    echo "Courier-Bold /usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf" >> /mapserver/fonts/fonts.list && \
    echo "Times-Roman /usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf" >> /mapserver/fonts/fonts.list && \
    echo "Times-Bold /usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf" >> /mapserver/fonts/fonts.list && \
    chown www-data:www-data /mapserver/fonts/fonts.list

# Copier le script de génération de mapfiles
COPY ckan-app/ckan/extensions/ckanext-ogc/scripts/generate-mapfile.py /usr/local/bin/generate-mapfile.py
RUN chmod +x /usr/local/bin/generate-mapfile.py

# Copier la configuration Apache pour MapServer
COPY mapserver/mapserver.conf /etc/apache2/conf-available/mapserver.conf
RUN a2enconf mapserver

# Exposer le port 80
EXPOSE 80

# Démarrer Apache
CMD ["apache2ctl", "-D", "FOREGROUND"]

