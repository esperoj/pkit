"""Wikidata source and declarative import plans.

Split by responsibility: extract.py turns entities into field values,
plans.py maps them onto record models, client.py fetches them. The public
surface matches the former single-module wikidata.py.
"""

from pkit.meta.wikidata.client import API_URL, WikidataSource
from pkit.meta.wikidata.extract import PRIMARY_LANGS, EntityView
from pkit.meta.wikidata.plans import (
    GAME_CLASSES,
    PERSON_PLAN,
    PLANS,
    SOFTWARE_CLASSES,
    SOFTWARE_PLAN,
    SOUND_CLASSES,
    SOUND_PLAN,
    TEXT_CLASSES,
    TEXT_PLAN,
    VIDEO_CLASSES,
    VIDEO_PLAN,
)

__all__ = [
    "API_URL",
    "GAME_CLASSES",
    "PERSON_PLAN",
    "PLANS",
    "PRIMARY_LANGS",
    "SOFTWARE_CLASSES",
    "SOFTWARE_PLAN",
    "SOUND_CLASSES",
    "SOUND_PLAN",
    "TEXT_CLASSES",
    "TEXT_PLAN",
    "VIDEO_CLASSES",
    "VIDEO_PLAN",
    "EntityView",
    "WikidataSource",
]
