# Configuration Kubernetes pour CKAN

## Variables d'environnement importantes

### Pour pygeoapi restart (dans ConfigMap)

Pour que le redémarrage de pygeoapi fonctionne correctement dans Kubernetes, configurez `PYGEOAPI_RESTART_CMD` si besoin. **Par défaut**, le code utilise le script qui privilégie l'API Kubernetes (sans kubectl ni docker dans le pod).

#### Option 1 : Script automatique (recommandé, défaut)

Le script `/srv/app/restart-pygeoapi.sh` détecte l'environnement et utilise en priorité l'**API Kubernetes** (fonctionne depuis un pod sans kubectl), puis kubectl si disponible. **Ne pas** définir `PYGEOAPI_RESTART_CMD="docker restart ..."` en Kubernetes.

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: ckan-config
  namespace: votre-namespace
data:
  PYGEOAPI_RESTART_CMD: "/srv/app/restart-pygeoapi.sh"
  # Optionnel : nom du deployment pygeoapi dans Kubernetes
  PYGEOAPI_DEPLOYMENT_NAME: "pygeoapi"
  # Optionnel : namespace (par défaut utilise le namespace du pod)
  KUBERNETES_NAMESPACE: "votre-namespace"
```

#### Option 2 : Utiliser kubectl directement

Si vous avez `kubectl` disponible dans le conteneur CKAN :

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: ckan-config
  namespace: votre-namespace
data:
  PYGEOAPI_RESTART_CMD: "kubectl rollout restart deployment/pygeoapi -n votre-namespace"
```

#### Option 3 : Désactiver le redémarrage automatique

Si vous préférez redémarrer pygeoapi manuellement :

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: ckan-config
  namespace: votre-namespace
data:
  PYGEOAPI_RESTART_CMD: ""  # Vide = pas de redémarrage automatique
```

### Application dans le Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ckan
  namespace: votre-namespace
spec:
  template:
    spec:
      containers:
      - name: ckan
        image: votre-registry/ckan:tag
        envFrom:
        - configMapRef:
            name: ckan-config
        # Ou directement :
        env:
        - name: PYGEOAPI_RESTART_CMD
          value: "/srv/app/restart-pygeoapi.sh"
        - name: PYGEOAPI_DEPLOYMENT_NAME
          value: "pygeoapi"
        - name: KUBERNETES_NAMESPACE
          valueFrom:
            fieldRef:
              fieldPath: metadata.namespace
```

## Permissions Kubernetes pour le script de redémarrage

Si vous utilisez l'option 1 (script automatique), le script essaiera d'utiliser l'API Kubernetes directement. Pour cela, le pod CKAN doit avoir un ServiceAccount avec les permissions appropriées :

### ServiceAccount et Role

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: ckan-serviceaccount
  namespace: votre-namespace
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: ckan-role
  namespace: votre-namespace
rules:
- apiGroups: ["apps"]
  resources: ["deployments"]
  verbs: ["get", "patch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: ckan-rolebinding
  namespace: votre-namespace
subjects:
- kind: ServiceAccount
  name: ckan-serviceaccount
  namespace: votre-namespace
roleRef:
  kind: Role
  name: ckan-role
  apiGroup: rbac.authorization.k8s.io
```

### Application dans le Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ckan
  namespace: votre-namespace
spec:
  template:
    spec:
      serviceAccountName: ckan-serviceaccount  # Ajouter cette ligne
      containers:
      - name: ckan
        # ... reste de la configuration
```

## Diagnostic en cas d'échec

Si les logs affichent « Commande de redémarrage non disponible (docker: not found), fallback Kubernetes » puis « Synchronisation pygeoapi terminée avec succès » mais pygeoapi n’a pas les nouvelles collections :

1. **Vérifier le namespace** : `KUBERNETES_NAMESPACE` doit correspondre au namespace où tourne pygeoapi (défaut : `ckan-bpm`).
2. **Vérifier le nom du deployment** : `PYGEOAPI_DEPLOYMENT_NAME` doit être le nom exact du deployment pygeoapi (défaut : `pygeoapi`).
3. **Vérifier les permissions** : le ServiceAccount du pod CKAN doit avoir un Role avec `patch` sur les deployments (voir section ci-dessus).
4. **Logs utiles** : après une synchro, chercher dans les logs :
   - `Redémarrage pygeoapi déclenché via API Kubernetes` → succès
   - `API Kubernetes: PATCH deployment X/Y → 403` → permissions insuffisantes
   - `Fallback K8s non possible (pod hors cluster ou ...)` → pod CKAN hors cluster ou token SA manquant

## Vérification

Pour vérifier que la configuration fonctionne :

```bash
# Vérifier que la variable d'environnement est définie
kubectl exec -n votre-namespace deployment/ckan -- env | grep PYGEOAPI_RESTART_CMD

# Tester le script de redémarrage manuellement
kubectl exec -n votre-namespace deployment/ckan -- /srv/app/restart-pygeoapi.sh

# Vérifier les logs pour voir quelle commande est utilisée
kubectl logs -n votre-namespace deployment/ckan | grep "pygeoapi restart command"
```

## Notes importantes

1. **Par défaut** : Si `PYGEOAPI_RESTART_CMD` n'est pas définie, le code utilise maintenant `/srv/app/restart-pygeoapi.sh` qui détecte automatiquement l'environnement.

2. **Dans Kubernetes** : Le script détecte automatiquement Kubernetes et utilise `kubectl rollout restart` ou l'API Kubernetes directement.

3. **Pas de redémarrage** : Si vous ne voulez pas de redémarrage automatique, laissez la variable vide ou définissez-la à `""`.

4. **Permissions** : Si le script ne peut pas redémarrer pygeoapi (pas de permissions Kubernetes), il affichera simplement un message d'avertissement mais ne fera pas échouer la synchronisation.











