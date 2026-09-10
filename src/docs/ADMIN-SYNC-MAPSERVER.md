# Guide d'utilisation de `admin_sync_mapserver`

L'action `admin_sync_mapserver` permet de lancer la génération des mapfiles MapServer pour tous les datasets géospatiaux CKAN.

## Prérequis

- **Droits administrateur** : L'utilisateur doit être `sysadmin` (administrateur système CKAN)
- **Token API** : Un token d'authentification CKAN valide (pour les appels en ligne de commande ou script)

## 1. Depuis le panneau d'administration CKAN

### Accès
1. Connectez-vous à CKAN en tant qu'administrateur
2. Accédez à la page : `/admin-tools/sync`
3. Cliquez sur le bouton **"Lancer la synchronisation MapServer"**

### Fonctionnement
- Le bouton lance une requête AJAX vers `/api/action/admin_sync_mapserver`
- Un message de succès/erreur s'affiche avec les résultats
- La sortie du script est affichée dans un encadré

## 2. Depuis la ligne de commande (curl)

### Commande de base
```bash
curl -X POST "https://ckan2.qualif-data.example.org/api/action/admin_sync_mapserver" \
  -H "Authorization: YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'
```

### Avec variables d'environnement
```bash
# Définir les variables
export CKAN_URL="https://ckan2.qualif-data.example.org"
export CKAN_TOKEN="your-api-token-here"

# Lancer la synchronisation
curl -X POST "${CKAN_URL}/api/action/admin_sync_mapserver" \
  -H "Authorization: ${CKAN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{}'
```

### Afficher la réponse formatée (JSON)
```bash
curl -X POST "${CKAN_URL}/api/action/admin_sync_mapserver" \
  -H "Authorization: ${CKAN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{}' | jq .
```

### Exemple de réponse
```json
{
  "success": true,
  "message": "Synchronisation MapServer terminée avec succès (15 mapfile(s) généré(s))",
  "output": "...",
  "success_count": 15,
  "error_count": 0
}
```

## 3. Depuis un script Python

### Script simple
```python
#!/usr/bin/env python3
"""Script pour lancer la synchronisation MapServer"""

import requests
import json
import os

# Configuration
CKAN_URL = os.getenv('CKAN_URL', 'https://ckan2.qualif-data.example.org')
CKAN_TOKEN = os.getenv('CKAN_TOKEN', '')

if not CKAN_TOKEN:
    print("Erreur: CKAN_TOKEN non défini")
    print("   Définir avec: export CKAN_TOKEN=your-token")
    exit(1)

# Appel API
url = f"{CKAN_URL}/api/action/admin_sync_mapserver"
headers = {
    "Authorization": CKAN_TOKEN,
    "Content-Type": "application/json"
}
data = {}

try:
    print(" Lancement de la synchronisation MapServer...")
    response = requests.post(url, headers=headers, json=data, timeout=600)
    response.raise_for_status()
    
    result = response.json()
    
    if result.get('success'):
        print(f"{result.get('message', 'Synchronisation réussie')}")
        if result.get('success_count'):
            print(f"   Mapfiles générés: {result['success_count']}")
        if result.get('error_count'):
            print(f"   Erreurs: {result['error_count']}")
        
        # Afficher la sortie si disponible
        if result.get('output'):
            print("\nSortie du script:")
            print(result['output'][:1000])  # Limiter à 1000 caractères
    else:
        print(f"Erreur: {result.get('error', 'Erreur inconnue')}")
        if result.get('output'):
            print(f"\nSortie du script:")
            print(result['output'][:1000])
        exit(1)
        
except requests.exceptions.Timeout:
    print("Timeout: La synchronisation prend trop de temps (>10 minutes)")
    exit(1)
except requests.exceptions.RequestException as e:
    print(f"Erreur de connexion: {e}")
    exit(1)
```

### Script avec gestion d'erreurs avancée
```python
#!/usr/bin/env python3
"""Script pour lancer la synchronisation MapServer avec gestion d'erreurs"""

import requests
import json
import os
import sys
from typing import Dict, Any

def sync_mapserver(ckan_url: str, ckan_token: str) -> Dict[str, Any]:
    """
    Lance la synchronisation MapServer
    
    Args:
        ckan_url: URL du serveur CKAN
        ckan_token: Token d'authentification CKAN
        
    Returns:
        Dictionnaire avec les résultats de la synchronisation
    """
    url = f"{ckan_url}/api/action/admin_sync_mapserver"
    headers = {
        "Authorization": ckan_token,
        "Content-Type": "application/json"
    }
    data = {}
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=600)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.Timeout:
        return {
            'success': False,
            'error': 'Timeout - la synchronisation prend trop de temps (>10 minutes)'
        }
    except requests.exceptions.RequestException as e:
        return {
            'success': False,
            'error': f'Erreur de connexion: {str(e)}'
        }

def main():
    """Fonction principale"""
    ckan_url = os.getenv('CKAN_URL', 'https://ckan2.qualif-data.example.org')
    ckan_token = os.getenv('CKAN_TOKEN', '')
    
    if not ckan_token:
        print("Erreur: CKAN_TOKEN non défini")
        print("   Définir avec: export CKAN_TOKEN=your-token")
        sys.exit(1)
    
    print(f" Lancement de la synchronisation MapServer...")
    print(f"   URL: {ckan_url}")
    
    result = sync_mapserver(ckan_url, ckan_token)
    
    if result.get('success'):
        print(f"{result.get('message', 'Synchronisation réussie')}")
        if result.get('success_count') is not None:
            print(f"   Mapfiles générés: {result['success_count']}")
        if result.get('error_count'):
            print(f"    Erreurs: {result['error_count']}")
        
        # Afficher la sortie si disponible
        if result.get('output'):
            print("\nSortie du script (dernières lignes):")
            output_lines = result['output'].split('\n')
            # Afficher les 50 dernières lignes
            for line in output_lines[-50:]:
                print(f"   {line}")
    else:
        print(f"Erreur: {result.get('error', 'Erreur inconnue')}")
        if result.get('output'):
            print(f"\nSortie du script (dernières lignes):")
            output_lines = result['output'].split('\n')
            for line in output_lines[-50:]:
                print(f"   {line}")
        sys.exit(1)

if __name__ == '__main__':
    main()
```

## 4. Depuis un script bash

### Script simple
```bash
#!/bin/bash
# Script pour lancer la synchronisation MapServer

set -euo pipefail

CKAN_URL="${CKAN_URL:-https://ckan2.qualif-data.example.org}"
CKAN_TOKEN="${CKAN_TOKEN:-}"

if [ -z "$CKAN_TOKEN" ]; then
    echo "Erreur: CKAN_TOKEN non défini"
    echo "   Définir avec: export CKAN_TOKEN=your-token"
    exit 1
fi

echo " Lancement de la synchronisation MapServer..."
echo "   URL: $CKAN_URL"

RESPONSE=$(curl -s -X POST "${CKAN_URL}/api/action/admin_sync_mapserver" \
  -H "Authorization: ${CKAN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{}' \
  -w "\n%{http_code}")

HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" != "200" ]; then
    echo "Erreur HTTP: $HTTP_CODE"
    echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
    exit 1
fi

SUCCESS=$(echo "$BODY" | jq -r '.success // false')
MESSAGE=$(echo "$BODY" | jq -r '.message // "N/A"')
SUCCESS_COUNT=$(echo "$BODY" | jq -r '.success_count // 0')
ERROR_COUNT=$(echo "$BODY" | jq -r '.error_count // 0')

if [ "$SUCCESS" = "true" ]; then
    echo "$MESSAGE"
    if [ "$SUCCESS_COUNT" != "0" ]; then
        echo "   Mapfiles générés: $SUCCESS_COUNT"
    fi
    if [ "$ERROR_COUNT" != "0" ]; then
        echo "    Erreurs: $ERROR_COUNT"
    fi
else
    ERROR=$(echo "$BODY" | jq -r '.error // "Erreur inconnue"')
    echo "Erreur: $ERROR"
    exit 1
fi
```

## 5. Depuis Docker/Kubernetes

### Docker
```bash
# Depuis l'extérieur du conteneur
docker exec ckan curl -X POST "http://localhost:5000/api/action/admin_sync_mapserver" \
  -H "Authorization: YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'

# Depuis l'intérieur du conteneur
docker exec -it ckan bash
curl -X POST "http://localhost:5000/api/action/admin_sync_mapserver" \
  -H "Authorization: YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'
```

### Kubernetes
```bash
# Obtenir le nom du pod CKAN
POD_NAME=$(kubectl get pods -l app=ckan -o jsonpath='{.items[0].metadata.name}')

# Lancer la synchronisation
kubectl exec $POD_NAME -- curl -X POST "http://localhost:5000/api/action/admin_sync_mapserver" \
  -H "Authorization: YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'
```

## Paramètres et comportement

### Paramètres de l'action
L'action `admin_sync_mapserver` ne prend **aucun paramètre** dans `data_dict` (objet vide `{}`).

### Comportement
- **Génération complète** : Si aucun dataset n'est spécifié, tous les datasets géospatiaux sont traités
- **Timeout** : 10 minutes maximum (600 secondes)
- **Options automatiques** :
  - `--use-datagis` : Utilise la base `datagis` pour la recherche de tables
  - `--auto-create-geometry` : Crée automatiquement les colonnes géométriques si nécessaire

### Variables d'environnement utilisées
Le script `generate-mapfile.py` utilise les variables d'environnement suivantes :
- `CKAN_URL` : URL du serveur CKAN (défaut: `http://ckan:5000`)
- `CKAN_API_KEY` : Clé API CKAN
- `POSTGRES_HOST` : Hôte PostGIS (défaut: `db`)
- `POSTGRES_PORT` : Port PostGIS (défaut: `5432`)
- `POSTGRES_DB` : Base de données PostGIS (défaut: `datastore`)
- `POSTGRES_USER` : Utilisateur PostGIS (défaut: `ckan`)
- `POSTGRES_PASSWORD` : Mot de passe PostGIS (défaut: `ckan`)
- `MAPFILES_DIR` : Répertoire des mapfiles (défaut: `/mapserver/mapfiles`)

## Résultats

### Succès
```json
{
  "success": true,
  "message": "Synchronisation MapServer terminée avec succès (15 mapfile(s) généré(s))",
  "output": "...",
  "success_count": 15,
  "error_count": 0
}
```

### Erreur
```json
{
  "success": false,
  "error": "Message d'erreur détaillé",
  "output": "..."
}
```

## Dépannage

### Erreur "Accès refusé"
- Vérifier que l'utilisateur est `sysadmin`
- Vérifier que le token API est valide

### Timeout
- La synchronisation peut prendre du temps pour de nombreux datasets
- Augmenter le timeout si nécessaire (modifier le code dans `plugin.py`)

### Script non trouvé
- Vérifier que `generate-mapfile.py` existe dans `/usr/local/bin/` ou dans les emplacements alternatifs

## Notes

- La synchronisation est **synchrone** : elle bloque jusqu'à la fin de l'exécution
- Pour de très grands volumes, considérer d'exécuter directement `generate-mapfile.py` avec des options spécifiques
- Les mapfiles sont générés dans `/mapserver/mapfiles/` (ou `MAPFILES_DIR` si défini)


