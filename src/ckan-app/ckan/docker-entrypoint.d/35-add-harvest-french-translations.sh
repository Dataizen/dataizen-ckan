#!/bin/bash
set -euo pipefail

echo "Ajout des traductions françaises pour ckanext-harvest..."

# Répertoires
I18N_DIR="/srv/app/src/ckanext-harvest/ckanext/harvest/i18n"
FR_DIR="${I18N_DIR}/fr/LC_MESSAGES"
POT_FILE="${I18N_DIR}/ckanext-harvest.pot"
PO_FILE="${FR_DIR}/ckanext-harvest.po"
MO_FILE="${FR_DIR}/ckanext-harvest.mo"

# Créer le répertoire français si nécessaire
if [ ! -d "$FR_DIR" ]; then
    echo "Création du répertoire français..."
    sudo mkdir -p "$FR_DIR" || mkdir -p "$FR_DIR"
    sudo chown -R ckan:ckan-sys "$FR_DIR" 2>/dev/null || chown -R ckan:ckan-sys "$FR_DIR" 2>/dev/null || true
fi

# Copier le fichier .pot vers .po si le fichier .po n'existe pas
if [ ! -f "$PO_FILE" ]; then
    echo "Création du fichier de traduction français..."
    if [ -f "$POT_FILE" ]; then
        sudo cp "$POT_FILE" "$PO_FILE" || cp "$POT_FILE" "$PO_FILE"
        sudo chown ckan:ckan-sys "$PO_FILE" 2>/dev/null || chown ckan:ckan-sys "$PO_FILE" 2>/dev/null || true
    else
        echo "Fichier template .pot non trouvé: $POT_FILE"
        exit 1
    fi
fi

# Mettre à jour l'en-tête du fichier .po pour le français
echo "Mise à jour de l'en-tête du fichier .po..."
sudo sed -i 's/Language-Team: LANGUAGE <LL@li.org>/Language-Team: French <fr@li.org>/' "$PO_FILE" || \
    sed -i 's/Language-Team: LANGUAGE <LL@li.org>/Language-Team: French <fr@li.org>/' "$PO_FILE"
sudo sed -i 's/"Language: \\n"/"Language: fr\\n"/' "$PO_FILE" || \
    sed -i 's/"Language: \\n"/"Language: fr\\n"/' "$PO_FILE"

# Ajouter les traductions françaises principales
echo "Ajout des traductions françaises..."

# Fonction pour ajouter/mettre à jour une traduction
update_translation() {
    local msgid="$1"
    local msgstr="$2"
    
    # Échapper les caractères spéciaux pour sed (sauf les accolades qui sont utilisées pour les placeholders)
    local escaped_msgid=$(echo "$msgid" | sed 's/[[\.*^$()+?|]/\\&/g')
    local escaped_msgstr=$(echo "$msgstr" | sed 's/[[\.*^$()+?|]/\\&/g' | sed 's/"/\\"/g')
    
    # Vérifier si la traduction existe déjà (utiliser grep avec -F pour éviter les problèmes d'échappement)
    if grep -Fq "msgid \"$msgid\"" "$PO_FILE"; then
        # Mettre à jour la traduction existante
        sudo sed -i "/^msgid \"$(echo "$msgid" | sed 's/[[\.*^$()+?|]/\\&/g')\"/,/^$/s/^msgstr \"\"/msgstr \"$escaped_msgstr\"/" "$PO_FILE" || \
            sed -i "/^msgid \"$(echo "$msgid" | sed 's/[[\.*^$()+?|]/\\&/g')\"/,/^$/s/^msgstr \"\"/msgstr \"$escaped_msgstr\"/" "$PO_FILE"
    else
        # Ajouter la traduction à la fin du fichier
        echo "" >> "$PO_FILE"
        echo "msgid \"$msgid\"" >> "$PO_FILE"
        echo "msgstr \"$msgstr\"" >> "$PO_FILE"
    fi
}

# Traductions principales
update_translation "Harvesting source successfully cleared" "Source de moissonnage vidée avec succès"
update_translation "Harvesting source successfully inactivated" "Source de moissonnage désactivée avec succès"
update_translation "Harvest source not found" "Source de moissonnage non trouvée"
update_translation "Harvest will start shortly. Refresh this page for updates." "Le moissonnage va démarrer sous peu. Actualisez cette page pour les mises à jour."
update_translation "Harvest source cleared" "Source de moissonnage vidée"
update_translation "Harvest job not found" "Tâche de moissonnage non trouvée"
update_translation "Add Harvest Source" "Ajouter une source de moissonnage"
update_translation "Last Harvest Job" "Dernière tâche de moissonnage"
update_translation "Harvest Sources" "Sources de moissonnage"
update_translation "Create Harvest Source" "Créer une source de moissonnage"
update_translation "Harvest sources" "Sources de moissonnage"
update_translation "Update frequency" "Fréquence de mise à jour"
update_translation "Harvest Jobs" "Tâches de moissonnage"
update_translation "Harvest" "Moissonnage"
update_translation "Harvest source" "Source de moissonnage"
update_translation "Harvest job" "Tâche de moissonnage"
update_translation "Harvest object" "Objet de moissonnage"
update_translation "Harvest now" "Moissonner maintenant"
update_translation "Clear" "Vider"
update_translation "Inactivate" "Désactiver"
update_translation "Activate" "Activer"
update_translation "Delete" "Supprimer"
update_translation "Edit" "Modifier"
update_translation "View" "Voir"
update_translation "Status" "Statut"
update_translation "Running" "En cours"
update_translation "Finished" "Terminé"
update_translation "New" "Nouveau"
update_translation "Error" "Erreur"
update_translation "Active" "Actif"
update_translation "Inactive" "Inactif"
update_translation "Manual" "Manuel"
update_translation "Daily" "Quotidien"
update_translation "Weekly" "Hebdomadaire"
update_translation "Monthly" "Mensuel"
update_translation "Biweekly" "Bimensuel"
update_translation "Always" "Toujours"
update_translation "A harvest job has already been scheduled for this source" "Une tâche de moissonnage a déjà été planifiée pour cette source"
update_translation "Cannot create new harvest jobs on inactive sources. First, please change the source status to active." "Impossible de créer de nouvelles tâches de moissonnage sur des sources inactives. Veuillez d'abord changer le statut de la source à actif."
update_translation "User {0} not authorized to create harvest sources" "L'utilisateur {0} n'est pas autorisé à créer des sources de moissonnage"
update_translation "Not authorized to see this page" "Non autorisé à voir cette page"
update_translation "Group not found" "Groupe non trouvé"
update_translation "Unauthorized to read group %s" "Non autorisé à lire le groupe %s"
update_translation "Groups" "Groupes"
update_translation "Tags" "Mots-clés"
update_translation "Formats" "Formats"
update_translation "Licence" "Licence"
update_translation "title" "titre"

# Compiler les traductions
echo "Compilation des traductions..."
PO_FILE_ESC="$PO_FILE"
MO_FILE_ESC="$MO_FILE"

if command -v msgfmt &> /dev/null; then
    sudo msgfmt -o "$MO_FILE" "$PO_FILE" || msgfmt -o "$MO_FILE" "$PO_FILE"
    sudo chown ckan:ckan-sys "$MO_FILE" 2>/dev/null || chown ckan:ckan-sys "$MO_FILE" 2>/dev/null || true
    echo "Traductions compilées avec msgfmt"
else
    # Essayer avec Python babel
    python3 << PYTHON_COMPILE || echo "Compilation Python échouée"
import os
import sys

po_file = "$PO_FILE_ESC"
mo_file = "$MO_FILE_ESC"

try:
    from babel.messages.pofile import read_po
    from babel.messages.mofile import write_mo
    
    with open(po_file, 'rb') as f:
        cat = read_po(f)
    
    with open(mo_file, 'wb') as f:
        write_mo(f, cat)
    print('Traductions compilées avec babel')
except ImportError:
    print('babel non disponible, compilation impossible')
    print('CKAN peut charger les fichiers .po directement, mais .mo est recommandé pour les performances')
except Exception as e:
    print(f'Erreur lors de la compilation: {e}')
    import traceback
    traceback.print_exc()
PYTHON_COMPILE
    if [ -f "$MO_FILE" ]; then
        sudo chown ckan:ckan-sys "$MO_FILE" 2>/dev/null || chown ckan:ckan-sys "$MO_FILE" 2>/dev/null || true
    fi
fi

echo "Traductions françaises ajoutées pour ckanext-harvest"
echo "Note: Vous devrez peut-être redémarrer CKAN pour que les traductions soient prises en compte"

