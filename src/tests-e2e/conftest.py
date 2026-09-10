"""
Pytest fixtures for the end-to-end ingestion suite.

These tests hit a real CKAN stack (default: http://localhost:8080) and
require an admin API token. They create a dedicated test organization,
upload a few resources of different formats, wait for the loaders, and
assert the expected side effects (datastore tables, mapfiles, OGC
endpoints). The org is cleaned up between runs so the suite is idempotent.

Configuration via environment variables (overridable per session):

- ``CKAN_URL``       : default ``http://localhost:8080``
- ``CKAN_API_KEY``   : default ``ckan-local-dev-apikey``
                       (the local ``ckan_admin`` token set by ``start-local.sh``)
- ``CKAN_E2E_ORG``   : default ``e2e-tests`` (created if missing)
- ``CKAN_E2E_TIMEOUT``: loader polling timeout in seconds, default ``180``
- ``CKAN_E2E_POLL``  : poll interval in seconds, default ``3``
"""
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import pytest
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


CKAN_URL = os.getenv('CKAN_URL', 'http://localhost:8080').rstrip('/')
CKAN_API_KEY = os.getenv('CKAN_API_KEY', 'ckan-local-dev-apikey')
ORG_NAME = os.getenv('CKAN_E2E_ORG', 'e2e-tests')
TIMEOUT = int(os.getenv('CKAN_E2E_TIMEOUT', '180'))
POLL = int(os.getenv('CKAN_E2E_POLL', '3'))

DATA_DIR = Path(__file__).parent / 'data'


# ---------------------------------------------------------------------------
# Session-scoped fixtures: CKAN reachability, test org
# ---------------------------------------------------------------------------

@pytest.fixture(scope='session')
def ckan_url() -> str:
    return CKAN_URL


@pytest.fixture(scope='session')
def api_key() -> str:
    return CKAN_API_KEY


@pytest.fixture(scope='session')
def session(api_key) -> requests.Session:
    """Session HTTP with retries.

    uwsgi workers may be respawning after a harakiri triggered by the
    synchronous OGC hook (see KNOWN-ISSUES.md, bug #4). We retry on
    transient connection errors and 5xx responses to keep the suite stable.
    """
    s = requests.Session()
    s.headers.update({'Authorization': api_key})
    retry = Retry(
        total=4, connect=4, read=4,
        status=4, status_forcelist=(502, 503, 504),
        backoff_factor=2.0,
        allowed_methods=frozenset(['HEAD', 'GET', 'POST']),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
    s.mount('http://', adapter)
    s.mount('https://', adapter)
    return s


@pytest.fixture(scope='session', autouse=True)
def _ckan_alive(session, ckan_url) -> None:
    """Abort the whole session early if CKAN is not reachable."""
    try:
        r = session.get(f'{ckan_url}/api/3/action/status_show', timeout=5)
        r.raise_for_status()
    except Exception as e:
        pytest.exit(f'CKAN not reachable at {ckan_url}: {e}', returncode=2)


@pytest.fixture(scope='session')
def test_org(session, ckan_url) -> Dict[str, Any]:
    """Ensure the e2e test organization exists; return its dict."""
    r = session.get(
        f'{ckan_url}/api/3/action/organization_show',
        params={'id': ORG_NAME},
        timeout=10,
    )
    if r.status_code == 200 and r.json().get('success'):
        return r.json()['result']

    create = session.post(
        f'{ckan_url}/api/3/action/organization_create',
        json={'name': ORG_NAME, 'title': 'E2E tests', 'description': 'Created by tests-e2e/'},
        timeout=15,
    )
    create.raise_for_status()
    return create.json()['result']


# ---------------------------------------------------------------------------
# Per-test dataset fixture with automatic cleanup
# ---------------------------------------------------------------------------

@pytest.fixture
def make_dataset(session, ckan_url, test_org) -> Callable[..., Dict[str, Any]]:
    """Factory creating a unique dataset per test, purged on teardown."""
    created: list = []

    def _create(name_prefix: str = 'e2e', **extra) -> Dict[str, Any]:
        name = f'{name_prefix}-{uuid.uuid4().hex[:8]}'
        payload = {
            'name': name,
            'title': f'E2E {name_prefix}',
            'owner_org': test_org['name'],
            'notes': 'Created by tests-e2e/',
            'private': False,
            **extra,
        }
        r = session.post(
            f'{ckan_url}/api/3/action/package_create',
            json=payload, timeout=15,
        )
        r.raise_for_status()
        pkg = r.json()['result']
        created.append(pkg['id'])
        return pkg

    yield _create

    for pkg_id in created:
        try:
            session.post(
                f'{ckan_url}/api/3/action/dataset_purge',
                json={'id': pkg_id}, timeout=15,
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Upload helper + polling utilities
# ---------------------------------------------------------------------------

@pytest.fixture
def upload_resource(session, ckan_url) -> Callable[..., Dict[str, Any]]:
    """Upload a local file as a resource on the given package.

    The OGC ``after_resource_create`` hook is currently synchronous and can
    exceed the uwsgi harakiri timeout (~50s) on the geospatial paths. When
    the HTTP call dies that way, the database row is already committed so
    we recover by polling ``package_show`` to retrieve the freshly created
    resource. Tests then continue with the usual waiters.
    """

    def _upload(package_id: str, file_path: Path, fmt: Optional[str] = None,
                name: Optional[str] = None) -> Dict[str, Any]:
        file_path = Path(file_path)
        resource_name = name or file_path.name
        data = {
            'package_id': package_id,
            'name': resource_name,
        }
        if fmt:
            data['format'] = fmt
        try:
            with file_path.open('rb') as fp:
                files = {'upload': (file_path.name, fp)}
                r = session.post(
                    f'{ckan_url}/api/3/action/resource_create',
                    data=data, files=files, timeout=45,
                )
            r.raise_for_status()
            return r.json()['result']
        except (requests.exceptions.ReadTimeout,
                requests.exceptions.ConnectionError) as e:
            # uwsgi harakiri probably killed the worker while the OGC hook
            # was running. The resource itself has been persisted before the
            # hook runs, so we look it up by name on the package.
            return _recover_resource_after_harakiri(
                session, ckan_url, package_id, resource_name,
            )

    return _upload


def _recover_resource_after_harakiri(session: requests.Session, ckan_url: str,
                                     package_id: str, resource_name: str,
                                     attempts: int = 12,
                                     delay: int = 5) -> Dict[str, Any]:
    for _ in range(attempts):
        r = session.get(
            f'{ckan_url}/api/3/action/package_show',
            params={'id': package_id}, timeout=15,
        )
        if r.status_code == 200 and r.json().get('success'):
            for res in r.json()['result'].get('resources', []):
                if res.get('name') == resource_name:
                    return res
        time.sleep(delay)
    pytest.fail(
        f'Resource {resource_name!r} not found on package {package_id} after '
        f'{attempts * delay}s ; worker harakiri without persistence.'
    )


def _resource_show(session: requests.Session, ckan_url: str, resource_id: str) -> Dict[str, Any]:
    r = session.get(
        f'{ckan_url}/api/3/action/resource_show',
        params={'id': resource_id}, timeout=10,
    )
    r.raise_for_status()
    return r.json()['result']


@pytest.fixture
def wait_for_datastore(session, ckan_url):
    """Wait until ``datastore_active`` becomes True on a resource."""

    def _wait(resource_id: str, timeout: int = TIMEOUT) -> Dict[str, Any]:
        deadline = time.time() + timeout
        last: Dict[str, Any] = {}
        while time.time() < deadline:
            last = _resource_show(session, ckan_url, resource_id)
            if last.get('datastore_active'):
                return last
            time.sleep(POLL)
        pytest.fail(
            f'Resource {resource_id} did not become datastore_active within '
            f'{timeout}s (last state: datastore_active={last.get("datastore_active")})'
        )

    return _wait


@pytest.fixture
def wait_for_condition():
    """Generic poll-until helper for assertions that may take time."""

    def _wait(predicate: Callable[[], bool], description: str,
              timeout: int = TIMEOUT, interval: int = POLL) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if predicate():
                    return
            except Exception:
                pass
            time.sleep(interval)
        pytest.fail(f'Condition not met within {timeout}s: {description}')

    return _wait


@pytest.fixture
def data_dir() -> Path:
    return DATA_DIR
