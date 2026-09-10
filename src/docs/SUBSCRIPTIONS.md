# Système d'abonnement CKAN - Guide d'intégration avec Drupal

## Vue d'ensemble

CKAN dispose d'un système d'abonnement (follow) intégré qui permet aux utilisateurs de suivre :
- **Des datasets** (packages)
- **Des organisations**
- **Des utilisateurs**

Les abonnements génèrent un **flux d'activité** (activity stream) que les utilisateurs peuvent consulter et pour lequel ils peuvent recevoir des notifications par email.

## API CKAN pour les abonnements

### Actions API disponibles

#### 1. Suivre un dataset
```bash
POST /api/action/follow_dataset
Authorization: {API_KEY}

{
  "id": "nom-du-dataset"
}
```

#### 2. Suivre une organisation
```bash
POST /api/action/follow_organization
Authorization: {API_KEY}

{
  "id": "nom-de-l-organisation"
}
```

#### 3. Suivre un utilisateur
```bash
POST /api/action/follow_user
Authorization: {API_KEY}

{
  "id": "nom-utilisateur"
}
```

#### 4. Ne plus suivre un dataset
```bash
POST /api/action/unfollow_dataset
Authorization: {API_KEY}

{
  "id": "nom-du-dataset"
}
```

#### 5. Ne plus suivre une organisation
```bash
POST /api/action/unfollow_organization
Authorization: {API_KEY}

{
  "id": "nom-de-l-organisation"
}
```

#### 6. Ne plus suivre un utilisateur
```bash
POST /api/action/unfollow_user
Authorization: {API_KEY}

{
  "id": "nom-utilisateur"
}
```

#### 7. Vérifier si un utilisateur suit un objet
```bash
GET /api/action/am_following_dataset?id=nom-du-dataset
Authorization: {API_KEY}

GET /api/action/am_following_organization?id=nom-de-l-organisation
Authorization: {API_KEY}

GET /api/action/am_following_user?id=nom-utilisateur
Authorization: {API_KEY}
```

#### 8. Obtenir la liste des abonnements d'un utilisateur
```bash
# Datasets suivis
GET /api/action/user_following_dataset_list?id=nom-utilisateur
Authorization: {API_KEY}

# Organisations suivies
GET /api/action/user_following_organization_list?id=nom-utilisateur
Authorization: {API_KEY}

# Utilisateurs suivis
GET /api/action/user_following_user_list?id=nom-utilisateur
Authorization: {API_KEY}
```

#### 9. Obtenir le nombre de followers
```bash
# Pour un dataset
GET /api/action/package_show?id=nom-du-dataset&include_num_followers=true
Authorization: {API_KEY}

# Pour une organisation
GET /api/action/organization_show?id=nom-de-l-organisation&include_num_followers=true
Authorization: {API_KEY}

# Pour un utilisateur
GET /api/action/user_show?id=nom-utilisateur&include_num_followers=true
Authorization: {API_KEY}
```

#### 10. Obtenir le flux d'activité
```bash
# Flux d'activité d'un utilisateur
GET /api/action/user_activity_list?id=nom-utilisateur
Authorization: {API_KEY}

# Flux d'activité d'un dataset
GET /api/action/package_activity_list?id=nom-du-dataset
Authorization: {API_KEY}

# Flux d'activité d'une organisation
GET /api/action/organization_activity_list?id=nom-de-l-organisation
Authorization: {API_KEY}

# Flux d'activité de ce que je suis
GET /api/action/followee_count
Authorization: {API_KEY}
```

## Notifications par email

### Configuration

Les notifications par email sont gérées par la configuration CKAN :

```ini
# Dans ckan.ini
ckan.activity_streams_enabled = true
ckan.activity_streams_email_notifications = true
```

### Statut de notification d'un utilisateur

```bash
# Vérifier le statut
GET /api/action/user_show?id=nom-utilisateur
Authorization: {API_KEY}

# La réponse inclut :
{
  "result": {
    "activity_streams_email_notifications": true,
    ...
  }
}
```

### Activer/désactiver les notifications

```bash
POST /api/action/user_update
Authorization: {API_KEY}

{
  "id": "nom-utilisateur",
  "activity_streams_email_notifications": true
}
```

## Intégration avec Drupal

### 1. Module Drupal requis

Installez le module **CKAN Connect** ou créez un module personnalisé :

```bash
composer require drupal/ckan_connect
drush en ckan_connect
```

### 2. Configuration du module

Dans Drupal, configurez la connexion à CKAN :

```php
// Dans settings.php ou via l'interface d'administration
$config['ckan_connect.settings']['ckan_url'] = 'https://ckan2.qualif-data.example.org';
$config['ckan_connect.settings']['ckan_api_key'] = 'votre-api-key';
```

### 3. Exemple d'intégration dans un module Drupal personnalisé

#### Créer un service pour gérer les abonnements

```php
<?php
// modules/custom/ckan_subscriptions/src/Service/CkanSubscriptionService.php

namespace Drupal\ckan_subscriptions\Service;

use Drupal\Core\Config\ConfigFactoryInterface;
use GuzzleHttp\ClientInterface;
use GuzzleHttp\Exception\RequestException;

class CkanSubscriptionService {

  protected $httpClient;
  protected $config;
  protected $ckanUrl;
  protected $apiKey;

  public function __construct(ClientInterface $http_client, ConfigFactoryInterface $config_factory) {
    $this->httpClient = $http_client;
    $this->config = $config_factory->get('ckan_subscriptions.settings');
    $this->ckanUrl = $this->config->get('ckan_url');
    $this->apiKey = $this->config->get('ckan_api_key');
  }

  /**
   * Suivre un dataset
   */
  public function followDataset($dataset_id, $user_api_key = NULL) {
    $api_key = $user_api_key ?: $this->apiKey;
    return $this->callCkanApi('follow_dataset', [
      'id' => $dataset_id,
    ], $api_key);
  }

  /**
   * Suivre une organisation
   */
  public function followOrganization($org_id, $user_api_key = NULL) {
    $api_key = $user_api_key ?: $this->apiKey;
    return $this->callCkanApi('follow_organization', [
      'id' => $org_id,
    ], $api_key);
  }

  /**
   * Ne plus suivre un dataset
   */
  public function unfollowDataset($dataset_id, $user_api_key = NULL) {
    $api_key = $user_api_key ?: $this->apiKey;
    return $this->callCkanApi('unfollow_dataset', [
      'id' => $dataset_id,
    ], $api_key);
  }

  /**
   * Ne plus suivre une organisation
   */
  public function unfollowOrganization($org_id, $user_api_key = NULL) {
    $api_key = $user_api_key ?: $this->apiKey;
    return $this->callCkanApi('unfollow_organization', [
      'id' => $org_id,
    ], $api_key);
  }

  /**
   * Vérifier si l'utilisateur suit un dataset
   */
  public function isFollowingDataset($dataset_id, $user_api_key) {
    try {
      $response = $this->httpClient->get($this->ckanUrl . '/api/action/am_following_dataset', [
        'headers' => [
          'Authorization' => $user_api_key,
        ],
        'query' => [
          'id' => $dataset_id,
        ],
      ]);
      $data = json_decode($response->getBody(), TRUE);
      return $data['result'] ?? FALSE;
    }
    catch (RequestException $e) {
      \Drupal::logger('ckan_subscriptions')->error('Error checking follow status: @error', [
        '@error' => $e->getMessage(),
      ]);
      return FALSE;
    }
  }

  /**
   * Obtenir les datasets suivis par un utilisateur
   */
  public function getUserFollowedDatasets($user_api_key) {
    try {
      $response = $this->httpClient->get($this->ckanUrl . '/api/action/user_following_dataset_list', [
        'headers' => [
          'Authorization' => $user_api_key,
        ],
      ]);
      $data = json_decode($response->getBody(), TRUE);
      return $data['result'] ?? [];
    }
    catch (RequestException $e) {
      \Drupal::logger('ckan_subscriptions')->error('Error fetching followed datasets: @error', [
        '@error' => $e->getMessage(),
      ]);
      return [];
    }
  }

  /**
   * Obtenir les organisations suivies par un utilisateur
   */
  public function getUserFollowedOrganizations($user_api_key) {
    try {
      $response = $this->httpClient->get($this->ckanUrl . '/api/action/user_following_organization_list', [
        'headers' => [
          'Authorization' => $user_api_key,
        ],
      ]);
      $data = json_decode($response->getBody(), TRUE);
      return $data['result'] ?? [];
    }
    catch (RequestException $e) {
      \Drupal::logger('ckan_subscriptions')->error('Error fetching followed organizations: @error', [
        '@error' => $e->getMessage(),
      ]);
      return [];
    }
  }

  /**
   * Obtenir le nombre de followers d'un dataset
   */
  public function getDatasetFollowersCount($dataset_id) {
    try {
      $response = $this->httpClient->get($this->ckanUrl . '/api/action/package_show', [
        'headers' => [
          'Authorization' => $this->apiKey,
        ],
        'query' => [
          'id' => $dataset_id,
          'include_num_followers' => TRUE,
        ],
      ]);
      $data = json_decode($response->getBody(), TRUE);
      return $data['result']['num_followers'] ?? 0;
    }
    catch (RequestException $e) {
      \Drupal::logger('ckan_subscriptions')->error('Error fetching followers count: @error', [
        '@error' => $e->getMessage(),
      ]);
      return 0;
    }
  }

  /**
   * Appel générique à l'API CKAN
   */
  protected function callCkanApi($action, $data, $api_key) {
    try {
      $response = $this->httpClient->post($this->ckanUrl . '/api/action/' . $action, [
        'headers' => [
          'Authorization' => $api_key,
          'Content-Type' => 'application/json',
        ],
        'json' => $data,
      ]);
      $result = json_decode($response->getBody(), TRUE);
      return $result['success'] ? $result['result'] : FALSE;
    }
    catch (RequestException $e) {
      \Drupal::logger('ckan_subscriptions')->error('Error calling CKAN API @action: @error', [
        '@action' => $action,
        '@error' => $e->getMessage(),
      ]);
      return FALSE;
    }
  }
}
```

#### Créer un formulaire pour s'abonner/désabonner

```php
<?php
// modules/custom/ckan_subscriptions/src/Form/SubscribeForm.php

namespace Drupal\ckan_subscriptions\Form;

use Drupal\Core\Form\FormBase;
use Drupal\Core\Form\FormStateInterface;
use Drupal\ckan_subscriptions\Service\CkanSubscriptionService;
use Symfony\Component\DependencyInjection\ContainerInterface;

class SubscribeForm extends FormBase {

  protected $ckanSubscriptionService;

  public function __construct(CkanSubscriptionService $ckan_subscription_service) {
    $this->ckanSubscriptionService = $ckan_subscription_service;
  }

  public static function create(ContainerInterface $container) {
    return new static(
      $container->get('ckan_subscriptions.service')
    );
  }

  public function getFormId() {
    return 'ckan_subscribe_form';
  }

  public function buildForm(array $form, FormStateInterface $form_state, $dataset_id = NULL, $org_id = NULL) {
    $user = \Drupal::currentUser();
    $user_api_key = $this->getUserApiKey($user->id());

    if (!$user_api_key) {
      $form['message'] = [
        '#markup' => '<p>Vous devez configurer votre clé API CKAN pour vous abonner.</p>',
      ];
      return $form;
    }

    $is_following = FALSE;
    $follow_type = NULL;

    if ($dataset_id) {
      $is_following = $this->ckanSubscriptionService->isFollowingDataset($dataset_id, $user_api_key);
      $follow_type = 'dataset';
      $follow_id = $dataset_id;
    }
    elseif ($org_id) {
      // Implémenter isFollowingOrganization de la même manière
      $follow_type = 'organization';
      $follow_id = $org_id;
    }

    $form['follow_type'] = [
      '#type' => 'hidden',
      '#value' => $follow_type,
    ];

    $form['follow_id'] = [
      '#type' => 'hidden',
      '#value' => $follow_id,
    ];

    $form['actions'] = [
      '#type' => 'actions',
    ];

    if ($is_following) {
      $form['actions']['unfollow'] = [
        '#type' => 'submit',
        '#value' => $this->t('Se désabonner'),
        '#submit' => ['::unfollowSubmit'],
      ];
    }
    else {
      $form['actions']['follow'] = [
        '#type' => 'submit',
        '#value' => $this->t('S\'abonner'),
        '#submit' => ['::followSubmit'],
      ];
    }

    return $form;
  }

  public function followSubmit(array &$form, FormStateInterface $form_state) {
    $user = \Drupal::currentUser();
    $user_api_key = $this->getUserApiKey($user->id());
    $follow_type = $form_state->getValue('follow_type');
    $follow_id = $form_state->getValue('follow_id');

    if ($follow_type === 'dataset') {
      $result = $this->ckanSubscriptionService->followDataset($follow_id, $user_api_key);
    }
    elseif ($follow_type === 'organization') {
      $result = $this->ckanSubscriptionService->followOrganization($follow_id, $user_api_key);
    }

    if ($result) {
      $this->messenger()->addStatus($this->t('Abonnement réussi !'));
    }
    else {
      $this->messenger()->addError($this->t('Erreur lors de l\'abonnement.'));
    }
  }

  public function unfollowSubmit(array &$form, FormStateInterface $form_state) {
    $user = \Drupal::currentUser();
    $user_api_key = $this->getUserApiKey($user->id());
    $follow_type = $form_state->getValue('follow_type');
    $follow_id = $form_state->getValue('follow_id');

    if ($follow_type === 'dataset') {
      $result = $this->ckanSubscriptionService->unfollowDataset($follow_id, $user_api_key);
    }
    elseif ($follow_type === 'organization') {
      $result = $this->ckanSubscriptionService->unfollowOrganization($follow_id, $user_api_key);
    }

    if ($result) {
      $this->messenger()->addStatus($this->t('Désabonnement réussi !'));
    }
    else {
      $this->messenger()->addError($this->t('Erreur lors du désabonnement.'));
    }
  }

  public function submitForm(array &$form, FormStateInterface $form_state) {
    // Ne pas utiliser cette méthode, utiliser les submit handlers spécifiques
  }

  protected function getUserApiKey($uid) {
    // Récupérer la clé API de l'utilisateur depuis le profil utilisateur
    // ou depuis une configuration personnalisée
    $user = \Drupal\user\Entity\User::load($uid);
    return $user->get('field_ckan_api_key')->value ?? NULL;
  }
}
```

### 4. Créer un bloc pour afficher les abonnements

```php
<?php
// modules/custom/ckan_subscriptions/src/Plugin/Block/MySubscriptionsBlock.php

namespace Drupal\ckan_subscriptions\Plugin\Block;

use Drupal\Core\Block\BlockBase;
use Drupal\Core\Plugin\ContainerFactoryPluginInterface;
use Symfony\Component\DependencyInjection\ContainerInterface;
use Drupal\ckan_subscriptions\Service\CkanSubscriptionService;

/**
 * @Block(
 *   id = "my_ckan_subscriptions",
 *   admin_label = @Translation("Mes abonnements CKAN"),
 * )
 */
class MySubscriptionsBlock extends BlockBase implements ContainerFactoryPluginInterface {

  protected $ckanSubscriptionService;

  public function __construct(array $configuration, $plugin_id, $plugin_definition, CkanSubscriptionService $ckan_subscription_service) {
    parent::__construct($configuration, $plugin_id, $plugin_definition);
    $this->ckanSubscriptionService = $ckan_subscription_service;
  }

  public static function create(ContainerInterface $container, array $configuration, $plugin_id, $plugin_definition) {
    return new static(
      $configuration,
      $plugin_id,
      $plugin_definition,
      $container->get('ckan_subscriptions.service')
    );
  }

  public function build() {
    $user = \Drupal::currentUser();
    $user_api_key = $this->getUserApiKey($user->id());

    if (!$user_api_key) {
      return [
        '#markup' => '<p>Configurez votre clé API CKAN pour voir vos abonnements.</p>',
      ];
    }

    $datasets = $this->ckanSubscriptionService->getUserFollowedDatasets($user_api_key);
    $organizations = $this->ckanSubscriptionService->getUserFollowedOrganizations($user_api_key);

    $build = [
      '#theme' => 'my_ckan_subscriptions',
      '#datasets' => $datasets,
      '#organizations' => $organizations,
      '#cache' => [
        'max-age' => 300, // Cache 5 minutes
      ],
    ];

    return $build;
  }

  protected function getUserApiKey($uid) {
    $user = \Drupal\user\Entity\User::load($uid);
    return $user->get('field_ckan_api_key')->value ?? NULL;
  }
}
```

## Cas d'usage

### 1. Notifications de mises à jour
- Quand un dataset est modifié, tous les followers reçoivent une notification
- Quand une organisation publie un nouveau dataset, les followers sont notifiés

### 2. Tableau de bord personnalisé
- Afficher les datasets/organisations suivis sur la page d'accueil Drupal
- Filtrer le contenu selon les abonnements de l'utilisateur

### 3. Recommandations
- Suggérer des datasets similaires à ceux suivis
- Proposer des organisations en fonction des intérêts

### 4. Analytics
- Suivre le nombre de followers par dataset/organisation
- Analyser les tendances d'abonnement

## Sécurité

### Gestion des clés API utilisateur

**Option 1 : Stocker dans le profil utilisateur Drupal**
```php
// Créer un champ personnalisé dans le profil utilisateur
// field_ckan_api_key (text, non exposé publiquement)
```

**Option 2 : Utiliser OAuth2/SSO**
- Si vous utilisez Keycloak ou un autre SSO, synchroniser les sessions
- Utiliser le token d'authentification pour les appels API

**Option 3 : Créer un mapping Drupal User → CKAN User**
- Stocker le nom d'utilisateur CKAN dans le profil Drupal
- Récupérer la clé API CKAN automatiquement via l'API

## Exemples d'utilisation depuis Drupal

### Exemple 1 : S'abonner à un dataset depuis un nœud Drupal

```php
// Dans un hook_node_view ou un contrôleur
$node = $variables['node'];
if ($node->hasField('field_ckan_dataset_id')) {
  $dataset_id = $node->get('field_ckan_dataset_id')->value;
  $subscription_service = \Drupal::service('ckan_subscriptions.service');
  $user_api_key = $this->getUserApiKey(\Drupal::currentUser()->id());
  
  $form = \Drupal::formBuilder()->getForm(
    'Drupal\ckan_subscriptions\Form\SubscribeForm',
    $dataset_id
  );
  
  $variables['subscribe_form'] = $form;
}
```

### Exemple 2 : Afficher les datasets suivis dans une vue

```php
// Créer une vue Drupal qui appelle l'API CKAN
// et affiche les datasets suivis par l'utilisateur connecté
```

### Exemple 3 : Webhook pour synchroniser les notifications

```php
// Créer un endpoint Drupal qui reçoit les webhooks CKAN
// et envoie des notifications aux utilisateurs Drupal
```

## Prochaines étapes

1. **Installer et configurer le module CKAN Connect** dans Drupal
2. **Créer un module personnalisé** pour gérer les abonnements
3. **Ajouter des champs** dans les profils utilisateur pour stocker les clés API CKAN
4. **Créer des blocs et formulaires** pour l'interface utilisateur
5. **Configurer les notifications** dans CKAN (email, webhooks)
6. **Tester l'intégration** avec des datasets et organisations réels

## Ressources

- [Documentation API CKAN - Follow](https://docs.ckan.org/en/latest/api/index.html#follow-dataset)
- [Module Drupal CKAN Connect](https://www.drupal.org/project/ckan_connect)
- [Module Drupal CKAN Sync](https://www.drupal.org/project/ckan_sync)





