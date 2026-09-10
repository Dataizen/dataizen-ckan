import logging
from keycloak import KeycloakOpenID, KeycloakAdmin
log = logging.getLogger(__name__)

class KeycloakClient:
    def __init__(self, server_url, client_id, realm_name, client_secret_key):
        self.server_url = server_url
        self.client_id = client_id
        self.realm_name = realm_name
        self.client_secret_key = client_secret_key
        
    def get_keycloak_client(self):
        return KeycloakOpenID(
            server_url=self.server_url, client_id=self.client_id, realm_name=self.realm_name, client_secret_key=self.client_secret_key
        )

    def get_auth_url(self, redirect_uri, scope=None):
        if scope is None:
            # Scope par défaut : seulement openid profile email (groups n'est pas toujours disponible)
            scope = "openid profile email"
        return self.get_keycloak_client().auth_url(redirect_uri=redirect_uri, scope=scope)

    def get_token(self, code, redirect_uri):
        return self.get_keycloak_client().token(grant_type="authorization_code", code=code, redirect_uri=redirect_uri)

    def get_user_info(self, token):
        return self.get_keycloak_client().userinfo(token.get('access_token'))

    def get_user_groups(self, token):
        """Récupère les groupes de l'utilisateur depuis userinfo"""
        return self.get_keycloak_client().userinfo(token.get('access_token')).get('groups', [])
    
    def get_user_groups_from_token(self, token):
        """Récupère les groupes depuis le token JWT décodé (si présents)"""
        import jwt
        try:
            # Décoder le token sans vérification (on fait confiance à Keycloak)
            decoded = jwt.decode(token.get('access_token'), options={"verify_signature": False})
            log.info("Token JWT décodé: {}".format(list(decoded.keys())))
            
            # Chercher les groupes dans différentes clés possibles
            groups = []
            
            # 1. Chercher directement 'groups'
            if 'groups' in decoded:
                groups = decoded['groups']
                log.debug("Groupes trouvés dans token JWT: {}".format(groups))
            
            # 2. Chercher dans resource_access (groupes de client)
            elif 'resource_access' in decoded:
                log.debug("resource_access trouvé: {}".format(decoded['resource_access']))
                # Parcourir tous les clients dans resource_access
                for client_name, client_data in decoded['resource_access'].items():
                    if 'groups' in client_data:
                        groups.extend(client_data['groups'])
                        log.debug("Groupes trouvés dans resource_access.{}: {}".format(client_name, client_data['groups']))
            
            # 3. Si pas de groupes, peut-être des rôles dans realm_access
            if 'realm_access' in decoded and 'roles' in decoded['realm_access']:
                roles = decoded['realm_access']['roles']
                log.debug("Pas de groupes, mais rôles trouvés: {}".format(roles))
                # On peut utiliser ces rôles comme groupes si nécessaire
                if not groups and roles:
                    log.info("Utilisation des rôles comme groupes: {}".format(roles))
                    # Filtrer seulement les rôles qui commencent par CKAN_
                    ckan_roles = [r for r in roles if r.startswith('CKAN_')]
                    if ckan_roles:
                        groups = ckan_roles
                        log.info("Utilisation des rôles CKAN comme groupes: {}".format(groups))
                
            return groups
        except Exception as e:
            log.error("Erreur lors du décodage JWT: {}".format(e))
            import traceback
            log.error("Traceback: {}".format(traceback.format_exc()))
            return []

    def get_keycloak_admin(self):
        return KeycloakAdmin(
            username="admin",
        )