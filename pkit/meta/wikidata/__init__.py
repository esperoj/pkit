"""Wikidata source: client.py fetches entities, extract.py turns them into
field values, plans.py maps them onto record models (QIDs and PIDs are
Wikidata's own, so plans stay data).
"""

from pkit.meta.wikidata.client import API_URL, WikidataSource

__all__ = ["API_URL", "WikidataSource"]
