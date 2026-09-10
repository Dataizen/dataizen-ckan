# Installation MapServer

## Packages Ubuntu

Le Dockerfile utilise les packages Ubuntu officiels :
- `cgi-mapserver` : Installe mapserv dans `/usr/lib/cgi-bin/mapserv`
- `mapserver-bin` : Binaires MapServer

## Si les packages ne sont pas disponibles

Si les packages `cgi-mapserver` ou `mapserver-bin` ne sont pas disponibles dans votre version d'Ubuntu, vous avez plusieurs options :

### Option 1 : Utiliser un PPA (recommandé)

Ajouter le PPA UbuntuGIS :

```dockerfile
RUN apt-get update && apt-get install -y software-properties-common && \
    add-apt-repository ppa:ubuntugis/ubuntugis-unstable && \
    apt-get update && \
    apt-get install -y cgi-mapserver mapserver-bin
```

### Option 2 : Compiler depuis les sources

Si les packages ne sont pas disponibles, vous pouvez compiler MapServer depuis les sources. Voir la [documentation officielle MapServer](https://mapserver.org/installation/unix.html#building-from-source).

### Option 3 : Utiliser une image Docker existante

Si l'installation depuis les packages échoue, vous pouvez utiliser une image Docker MapServer existante et l'adapter :

```dockerfile
FROM kartoza/mapserver:latest
# ou
FROM camptocamp/mapserver:latest
```

## Vérification de l'installation

Après la construction de l'image, vérifier que mapserv est disponible :

```bash
docker build -t mapserver-test mapserver/
docker run --rm mapserver-test which mapserv
docker run --rm mapserver-test mapserv -v
```

## Dépannage

Si l'installation échoue :

1. Vérifier les logs de construction : `docker build mapserver/ 2>&1 | tee build.log`
2. Vérifier les packages disponibles : `docker run --rm ubuntu:22.04 apt-cache search mapserver`
3. Consulter la [documentation MapServer](https://mapserver.org/)



