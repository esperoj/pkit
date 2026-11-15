"""Wikidata source and declarative import plans.

Split by responsibility: extract.py turns entities into field values,
plans.py maps them onto record models, client.py fetches them. The public
surface matches the former single-module wikidata.py, with the *_CLASSES
tables folded into the *_KINDS mappings (one QID -> kind table per plan
now drives both matching and kind).
"""

from pkit.meta.wikidata.client import API_URL, WikidataSource
from pkit.meta.wikidata.extract import PRIMARY_LANGS, EntityView
from pkit.meta.wikidata.plans import (
    GAME_KINDS,
    PERSON_PLAN,
    PLANS,
    SOFTWARE_KINDS,
    SOFTWARE_PLAN,
    SOUND_KINDS,
    SOUND_PLAN,
    TEXT_KINDS,
    TEXT_PLAN,
    VIDEO_KINDS,
    VIDEO_PLAN,
)

__all__ = [
    "API_URL",
    "GAME_KINDS",
    "PERSON_PLAN",
    "PLANS",
    "PRIMARY_LANGS",
    "SOFTWARE_KINDS",
    "SOFTWARE_PLAN",
    "SOUND_KINDS",
    "SOUND_PLAN",
    "TEXT_KINDS",
    "TEXT_PLAN",
    "VIDEO_KINDS",
    "VIDEO_PLAN",
    "EntityView",
    "WikidataSource",
]
