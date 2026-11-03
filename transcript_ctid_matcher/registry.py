"""Client for the Credential Engine Registry Search API (CTDL JSON queries).

Endpoint reference: https://credreg.net/registry/searchapi
  POST {base}/assistant/search/ctdl
  Headers: Authorization: Bearer <api key>
  Body: {"Query": {...}, "Skip": N, "Take": N}
  Response: {"data": [...], "valid": true, "extra": {"TotalResults": N}}
"""

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

import requests

from .config import SEARCH_ENDPOINTS

log = logging.getLogger(__name__)

ORG_TYPES = ["ceterms:CredentialOrganization", "ceterms:QACredentialOrganization"]


def langstring(value: Any) -> Optional[str]:
    """CTDL language maps look like {"en-US": "..."} (values may be lists)."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for v in value.values():
            if isinstance(v, list):
                return v[0] if v else None
            return v
    if isinstance(value, list):
        return langstring(value[0]) if value else None
    return str(value)


def langstring_all(value: Any) -> list[str]:
    """All strings in a language map / list-of-language-maps."""
    out: list[str] = []
    if value is None:
        return out
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        for v in value.values():
            out.extend(v if isinstance(v, list) else [v])
        return out
    if isinstance(value, list):
        for item in value:
            out.extend(langstring_all(item))
    return out


class RegistryClient:
    def __init__(self, api_key: str, environment: str = "production",
                 cache_dir: Optional[Path] = None):
        self.endpoint = SEARCH_ENDPOINTS[environment]
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })
        self.cache_dir = cache_dir

    # -- low level ----------------------------------------------------------

    def search(self, query: dict, skip: int = 0, take: int = 20,
               sort: Optional[str] = None) -> dict:
        body: dict[str, Any] = {"Query": query, "Skip": skip, "Take": take}
        if sort:
            body["Sort"] = sort
        for attempt in range(4):
            resp = self.session.post(self.endpoint, json=body, timeout=60)
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = 2 ** attempt
                log.warning("Registry API %s; retrying in %ss", resp.status_code, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("valid") is False:
                raise RuntimeError(f"Registry query rejected: {payload.get('status')}")
            return payload
        resp.raise_for_status()
        return {}

    def search_all(self, query: dict, max_results: int = 3000) -> list[dict]:
        """Paginate through all results for a query (Take max is 100)."""
        results: list[dict] = []
        skip = 0
        while skip < max_results:
            payload = self.search(query, skip=skip, take=100)
            page = payload.get("data") or []
            results.extend(page)
            total = (payload.get("extra") or {}).get("TotalResults", len(results))
            skip += 100
            if skip >= total or not page:
                break
        return results

    # -- caching ------------------------------------------------------------

    def _cached(self, key: str, fetch) -> list[dict]:
        if not self.cache_dir:
            return fetch()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        f = self.cache_dir / f"registry-{key}.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
        data = fetch()
        f.write_text(json.dumps(data), encoding="utf-8")
        return data

    def fetch_resource(self, uri: str) -> Optional[dict]:
        """Fetch a single registry resource by URI (public, no auth needed).

        Used to resolve competency references that alignment objects carry
        only as target URIs. Returns None on any failure.
        """
        ctid = uri.rstrip("/").rsplit("/", 1)[-1]
        if not ctid.startswith("ce-"):
            return None

        cache_file = None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file = self.cache_dir / f"registry-resource-{ctid}.json"
            if cache_file.exists():
                return json.loads(cache_file.read_text(encoding="utf-8"))
        try:
            resp = self.session.get(uri, timeout=30)
            resp.raise_for_status()
            record = resp.json()
        except Exception as exc:  # don't cache failures; retry next run
            log.warning("Could not fetch resource %s: %s", uri, exc)
            return None
        if cache_file:
            cache_file.write_text(json.dumps(record), encoding="utf-8")
        return record

    # -- high level ---------------------------------------------------------

    def org_search(self, term: str, match_type: Optional[str] = None,
                   take: int = 40) -> list[dict]:
        """One organization search by name/alternateName.

        match_type: None for default full-text matching, or a search:matchType
        token such as "search:exactMatch" / "search:contains". The OR terms
        must be wrapped in a search:termGroup: a top-level search:operator
        would OR the @type filter too, matching nearly every record.
        """
        value = ({"search:value": term, "search:matchType": match_type}
                 if match_type else term)
        query = {
            "@type": ORG_TYPES,
            "search:termGroup": {
                "search:operator": "search:orTerms",
                "ceterms:name": value,
                "ceterms:alternateName": value,
            },
        }
        payload = self.search(query, take=take, sort="search:relevance")
        return payload.get("data") or []

    def resources_for_org(self, org_uri: str, org_ctid: str,
                          types, label: str) -> list[dict]:
        """All resources of the given types owned OR offered by an organization.

        Runs two queries (ownedBy, offeredBy) and merges by @id to avoid
        relying on operator-grouping semantics.
        """
        def fetch() -> list[dict]:
            merged: dict[str, dict] = {}
            # The registry has two resource-URI styles (/resources/ and
            # /ce-registry/resources/), so a URI-valued ownedBy query can miss
            # records that reference the other style. The nested-CTID form
            # traverses into the referenced org and is URI-style agnostic.
            refs = [org_uri]
            if org_ctid and org_ctid.startswith("ce-"):
                refs.append({"ceterms:ctid": org_ctid})
            for prop in ("ceterms:ownedBy", "ceterms:offeredBy"):
                for ref in refs:
                    try:
                        for rec in self.search_all({"@type": types, prop: ref}):
                            rid = rec.get("@id") or rec.get("ceterms:ctid") or repr(rec)[:80]
                            merged[rid] = rec
                    except Exception as exc:  # keep whichever queries worked
                        log.warning("Query %s=%s for %s failed: %s",
                                    prop, ref, org_ctid, exc)
            return list(merged.values())

        return self._cached(f"{label}-{org_ctid}", fetch)

    def courses_for_org(self, org_uri: str, org_ctid: str) -> list[dict]:
        # Course-like records are published inconsistently as ceterms:Course,
        # ceterms:LearningProgram, or plain ceterms:LearningOpportunityProfile;
        # subClassOf on the LOP superclass covers all of them.
        type_filter = {
            "search:value": "ceterms:LearningOpportunityProfile",
            "search:matchType": "search:subClassOf",
        }
        return self.resources_for_org(org_uri, org_ctid, type_filter, "courses-v2")

    def credentials_for_org(self, org_uri: str, org_ctid: str) -> list[dict]:
        # Registry records are typed as concrete subclasses (BachelorDegree,
        # Certificate, ...); search:subClassOf covers the whole Credential tree.
        # Cache label is versioned past "credentials", which cached empty
        # results from the earlier bare ceterms:Credential query.
        type_filter = {
            "search:value": "ceterms:Credential",
            "search:matchType": "search:subClassOf",
        }
        return self.resources_for_org(org_uri, org_ctid, type_filter, "credentials-v3")
