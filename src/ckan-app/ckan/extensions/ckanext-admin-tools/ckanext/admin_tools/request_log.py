"""
Enregistrement des requêtes CKAN (path, IP, User-Agent, etc.) pour affichage dans l'admin.
Stockage dans Redis (liste bornée) pour éviter la croissance infinie.
"""
import json
import logging
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

REDIS_REQUEST_LOG_KEY = "ckan:admin:request_log"
MAX_REQUEST_LOG_ENTRIES = 500


def get_redis():
    """Connexion Redis (même que pour les jobs)."""
    try:
        from ckan.lib.redis import connect_to_redis
        return connect_to_redis()
    except Exception:
        pass
    try:
        import os
        from ckan.common import config
        url = config.get('ckan.redis.url') or os.getenv('CKAN_REDIS_URL')
        if url:
            import redis
            return redis.from_url(url)
    except Exception:
        pass
    return None


def _is_private_or_internal(ip: str) -> bool:
    """True si l'IP est privée/loopback/CGNAT (100.64.0.0/10)."""
    if not ip or not str(ip).strip():
        return True
    try:
        import ipaddress
        a = ipaddress.ip_address(str(ip).strip())
        if a.is_private or a.is_loopback:
            return True
        # 100.64.0.0/10 = CGNAT / réseau partagé (souvent le proxy LB)
        return a in ipaddress.ip_network('100.64.0.0/10')
    except Exception:
        return False


def _get_client_ip(request) -> str:
    """
    IP du client. Privilégie les en-têtes renseignés par le reverse proxy
    (X-Real-IP, CF-Connecting-IP, True-Client-IP), puis X-Forwarded-For
    en ignorant les IPs internes (100.x, 10.x, 172.16.x, 192.168.x), puis remote_addr.
    """
    if not hasattr(request, 'headers'):
        return (getattr(request, 'remote_addr', None) or '') if request else ''

    h = request.headers

    # En-têtes souvent renseignés par le proxy avec la vraie IP client (prioritaires)
    for name in ('X-Real-IP', 'CF-Connecting-IP', 'True-Client-IP'):
        v = h.get(name)
        if v:
            v = v.split(',')[0].strip()
            if v:
                return v

    # X-Forwarded-For : liste d'IPs. Selon les setups, le client est à gauche ou à droite.
    # On cherche la première IP "publique" (non 10/172.16/192.168/100.64/127).
    forwarded = h.get('X-Forwarded-For')
    if forwarded:
        ips = [x.strip() for x in forwarded.split(',') if x.strip()]
        if ips:
            # D'abord la dernière (client réel quand les proxies préfixent)
            for ip in reversed(ips):
                if not _is_private_or_internal(ip):
                    return ip
            # Sinon la première (client quand les proxies suffixent)
            for ip in ips:
                if not _is_private_or_internal(ip):
                    return ip
            return ips[0]

    raw = getattr(request, 'remote_addr', None) or ''
    return raw if raw else ''


def _get_full_url(request) -> str:
    """URL complète de la requête (méthode + chemin + query)."""
    if not hasattr(request, 'url'):
        return ''
    return request.url or ''


def push_request(request, status_code: int, duration_seconds: float) -> None:
    """
    Enregistre une requête dans Redis.
    path, method, status, ip, user_agent, referer, url, timestamp, duration.
    """
    conn = get_redis()
    if not conn:
        return
    try:
        ip = _get_client_ip(request)
        user_agent = (request.headers.get('User-Agent', '') or '')[:200] if hasattr(request, 'headers') else ''
        referer = (request.headers.get('Referer', '') or '')[:500] if hasattr(request, 'headers') else ''
        path = getattr(request, 'path', '') or ''
        method = getattr(request, 'method', '') or 'GET'
        full_url = _get_full_url(request)

        # Type d'accès: API vs UI (approximatif)
        access_type = 'API' if path.startswith('/api/') else 'UI'

        entry = {
            'path': path,
            'method': method,
            'status': status_code,
            'ip': ip,
            'user_agent': user_agent,
            'referer': referer,
            'url': full_url,
            'ts': time.time(),
            'duration': round(duration_seconds, 3),
            'access_type': access_type,
        }
        raw = json.dumps(entry, ensure_ascii=False)
        conn.lpush(REDIS_REQUEST_LOG_KEY, raw)
        conn.ltrim(REDIS_REQUEST_LOG_KEY, 0, MAX_REQUEST_LOG_ENTRIES - 1)
    except Exception as e:
        log.debug("request_log push_request: %s", e)


def get_request_log(limit: int = 100) -> List[Dict[str, Any]]:
    """Retourne les N dernières requêtes (ordre chronologique inverse = plus récent en premier)."""
    conn = get_redis()
    if not conn:
        return []
    try:
        raw_list = conn.lrange(REDIS_REQUEST_LOG_KEY, 0, limit - 1)
        out = []
        for raw in raw_list or []:
            try:
                out.append(json.loads(raw))
            except Exception:
                pass
        return out
    except Exception as e:
        log.debug("request_log get_request_log: %s", e)
        return []
