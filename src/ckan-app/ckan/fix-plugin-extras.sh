#!/bin/bash

echo "Script de maintenance plugin_extras pour Keycloak..."

# Charger les variables d'environnement
if [ -f "/srv/app/.env" ]; then
    export $(grep -v '^#' /srv/app/.env | xargs)
fi

# Exécuter le nettoyage
python3 -c "
import ckan.model as model
from ckan.model import User
import json

print('Recherche des utilisateurs avec plugin_extras corrompus...')

try:
    users = model.Session.query(User).filter(User.plugin_extras.isnot(None)).all()
    fixed_count = 0
    
    for user in users:
        try:
            extras = user.plugin_extras
            
            # Vérifier si c'est une chaîne (corrompu)
            if isinstance(extras, str):
                print(f'Correction utilisateur {user.name}: plugin_extras était une chaîne')
                user.plugin_extras = None
                fixed_count += 1
            # Vérifier si c'est un dict avec 'idp': 'google' (incorrect)
            elif isinstance(extras, dict) and extras.get('idp') == 'google':
                print(f'Correction utilisateur {user.name}: idp était google au lieu de keycloak')
                user.plugin_extras = {'idp': 'keycloak'}
                fixed_count += 1
            # Vérifier si c'est un dict valide mais sans idp
            elif isinstance(extras, dict) and 'idp' not in extras:
                print(f'Correction utilisateur {user.name}: ajout de idp keycloak')
                user.plugin_extras = {'idp': 'keycloak'}
                fixed_count += 1
                
        except Exception as e:
            print(f'Erreur avec utilisateur {user.name}: {e}')
            user.plugin_extras = None
            fixed_count += 1
    
    if fixed_count > 0:
        model.Session.commit()
        print(f'{fixed_count} utilisateurs corrigés')
    else:
        print('Aucun problème détecté')
        
except Exception as e:
    print(f'Erreur lors du nettoyage: {e}')
"

echo "Maintenance terminée"
