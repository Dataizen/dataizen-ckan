#!/usr/bin/env python3
"""
Script pour créer une vue cartographique personnalisée pour les CSV avec coordonnées
"""

import requests
import json
import os
import sys

def create_csv_map_view(resource_id, api_key, ckan_url=None):
    ckan_url = ckan_url or os.getenv("CKAN_URL", "http://127.0.0.1:5000")
    """
    Crée une vue HTML personnalisée qui affiche une carte Leaflet 
    pour un CSV avec colonnes latitude/longitude
    """
    
    # Template HTML avec Leaflet pour afficher les points CSV
    html_template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Carte des Données</title>
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.7.1/dist/leaflet.css" />
        <style>
            #map { height: 400px; width: 100%; }
            .popup-content { font-size: 14px; }
        </style>
    </head>
    <body>
        <div id="map"></div>
        <script src="https://unpkg.com/leaflet@1.7.1/dist/leaflet.js"></script>
        <script>
            // Initialiser la carte
            var map = L.map('map').setView([47.0, 5.5], 8);
            
            // Ajouter la couche de base
            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                attribution: '© OpenStreetMap contributors'
            }).addTo(map);
            
            // Charger les données depuis l'API CKAN
            fetch('/api/action/datastore_search?resource_id={{resource_id}}&limit=1000')
                .then(response => response.json())
                .then(data => {
                    if (data.success && data.result.records) {
                        var bounds = [];
                        data.result.records.forEach(function(record) {
                            var lat = parseFloat(record.latitude);
                            var lng = parseFloat(record.longitude);
                            
                            if (!isNaN(lat) && !isNaN(lng)) {
                                var marker = L.marker([lat, lng]).addTo(map);
                                
                                // Créer le popup avec les données
                                var popupContent = '<div class="popup-content">';
                                Object.keys(record).forEach(function(key) {
                                    if (key !== '_id' && record[key]) {
                                        popupContent += '<strong>' + key + ':</strong> ' + record[key] + '<br>';
                                    }
                                });
                                popupContent += '</div>';
                                
                                marker.bindPopup(popupContent);
                                bounds.push([lat, lng]);
                            }
                        });
                        
                        // Ajuster la vue pour inclure tous les points
                        if (bounds.length > 0) {
                            map.fitBounds(bounds, {padding: [20, 20]});
                        }
                    }
                })
                .catch(error => {
                    console.error('Erreur lors du chargement des données:', error);
                    document.getElementById('map').innerHTML = 
                        '<p>Erreur lors du chargement des données cartographiques.</p>';
                });
        </script>
    </body>
    </html>
    """
    
    # Remplacer le placeholder par l'ID réel de la ressource
    html_content = html_template.replace('{{resource_id}}', resource_id)
    
    # Créer la vue avec du contenu HTML personnalisé
    view_data = {
        "resource_id": resource_id,
        "title": "Carte Interactive CSV",
        "description": "Visualisation cartographique des données CSV avec coordonnées",
        "view_type": "text_view",
        "text": html_content
    }
    
    headers = {
        'Authorization': api_key,
        'Content-Type': 'application/json'
    }
    
    response = requests.post(
        f"{ckan_url}/api/action/resource_view_create",
        headers=headers,
        data=json.dumps(view_data)
    )
    
    if response.status_code == 200:
        result = response.json()
        if result.get('success'):
            print(f"Vue cartographique créée avec succès ! ID: {result['result']['id']}")
            return result['result']['id']
        else:
            print(f"Erreur CKAN: {result.get('error', 'Erreur inconnue')}")
    else:
        print(f"Erreur HTTP {response.status_code}: {response.text}")
    
    return None

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python create-csv-map-view.py <resource_id>")
        sys.exit(1)
    
    resource_id = sys.argv[1]
    api_key = os.getenv("CKAN_API_KEY")
    if not api_key:
        print("Définir CKAN_API_KEY (jeton CKAN) dans l'environnement.")
        sys.exit(1)

    create_csv_map_view(resource_id, api_key)












