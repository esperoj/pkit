"""Declarative import plans: one Plan per record model.

Adding a type means adding its classes, kind mapping, and Plan here —
nothing else. QIDs and PIDs are Wikidata's own, so plans stay data.
"""

from pkit.meta.models import Person, Software, Sound, Text, Video
from pkit.meta.source import LinkRef, Plan, first
from pkit.meta.wikidata.extract import (
    EntityView,
    claim,
    claims,
    description,
    ext_ids,
    has_claim,
    has_class,
    i18n_extra,
    item_ids,
    kind_of,
    person_name,
    work_title,
)

__all__ = [
    "GAME_CLASSES",
    "PERSON_PLAN",
    "PLANS",
    "SOFTWARE_CLASSES",
    "SOFTWARE_PLAN",
    "SOUND_CLASSES",
    "SOUND_PLAN",
    "TEXT_CLASSES",
    "TEXT_PLAN",
    "VIDEO_CLASSES",
    "VIDEO_PLAN",
]

GAME_CLASSES = (
    "Q7889",  # video game
    "Q7058673",  # video game series
    "Q689445",  # visual novel
    "Q209163",  # expansion add-on
    "Q865493",  # video game mod
)

SOFTWARE_CLASSES = (
    "Q7397",  # software
    "Q166142",  # application software
    "Q341",  # free software
    "Q506883",  # free and open-source software
    "Q1130645",  # open-source software
    "Q218616",  # proprietary software
    "Q188860",  # software library
    "Q9135",  # operating system
    "Q189210",  # web application
)

TEXT_CLASSES = (
    "Q571",  # book
    "Q7725634",  # literary work
    "Q47461344",  # written work
    "Q3331189",  # version, edition or translation
    "Q8261",  # novel
    "Q49084",  # short story
    "Q25379",  # play
    "Q277759",  # book series
    "Q1667921",  # novel series
    "Q8274",  # manga
    "Q21198342",  # manga series
    "Q747381",  # light novel
    "Q53731850",  # light novel series
    "Q104213567",  # light novel series
    "Q725377",  # graphic novel
    "Q1760610",  # comic book
    "Q2831984",  # comic book album
    "Q1004",  # comics
    "Q191067",  # article
    "Q13442814",  # scholarly article
    "Q5633421",  # scientific journal
    "Q737498",  # academic journal
    "Q1057179",  # user guide / manual
    "Q1413406",  # technical documentation
)

VIDEO_CLASSES = (
    "Q11424",  # film
    "Q24856",  # film series
    "Q5398426",  # television series
    "Q581714",  # animated series
    "Q1107",  # anime
    "Q21191270",  # television series episode
    "Q1464125",  # web series episode
    "Q110879246",  # video work
)

SOUND_CLASSES = (
    "Q7366",  # song
    "Q482994",  # album
    "Q134556",  # single
    "Q169930",  # extended play
    "Q24634210",  # podcast show
    "Q61855877",  # podcast episode
)

_GAME_ID_PROPS = (
    "P1733",  # Steam application ID
    "P5794",  # IGDB game ID
    "P2725",  # GOG application ID
    "P11688",  # MobyGames game ID
    "P3180",  # Visual Novel Database ID
    "P6337",  # PCGamingWiki ID
    "P4769",  # GameFAQs game ID
    "P5290",  # Redump ID
    "P7597",  # Lutris game ID
)

_SOFTWARE_PROPS = (
    "P306",  # operating system
    "P1324",  # source code repository URL
    "P4655",  # Flathub ID
    "P3454",  # Arch Linux package
    "P3442",  # Debian stable package
    "P3597",  # F-Droid package
    "P4763",  # crates.io ID
)

_TEXT_ID_PROPS = ("P212", "P957", "P236", "P356")  # ISBN-13, ISBN-10, ISSN, DOI

_VIDEO_ID_PROPS = ("P345", "P4947", "P4983")  # IMDb ID, TMDB movie ID, TMDB TV series ID

_SOUND_ID_PROPS = ("P436", "P1243")  # MusicBrainz release group ID, ISRC

_SOFTWARE_KINDS = {
    "Q188860": "library",
    "Q9135": "os",
}

_TEXT_KINDS = {
    "Q8274": "manga",
    "Q21198342": "manga",
    "Q747381": "light novel",
    "Q53731850": "light novel",
    "Q104213567": "light novel",
    "Q1004": "comic",
    "Q1760610": "comic",
    "Q2831984": "comic",
    "Q725377": "comic",
    "Q25379": "play",
    "Q49084": "short story",
    "Q8261": "novel",
    "Q1667921": "novel",
    "Q13442814": "article",
    "Q191067": "article",
    "Q5633421": "journal",
    "Q737498": "journal",
    "Q1057179": "manual",
    "Q1413406": "manual",
    "Q571": "book",
    "Q277759": "book",
    "Q3331189": "book",
}

_VIDEO_KINDS = {
    "Q11424": "movie",
    "Q24856": "series",
    "Q5398426": "series",
    "Q581714": "series",
    "Q1107": "anime",
    "Q21191270": "episode",
    "Q1464125": "episode",
}

_SOUND_KINDS = {
    "Q7366": "song",
    "Q482994": "album",
    "Q134556": "single",
    "Q169930": "ep",
    "Q24634210": "podcast",
    "Q61855877": "episode",
}


def _software_kind(view: EntityView) -> str:
    if has_class(*GAME_CLASSES)(view) or has_claim(*_GAME_ID_PROPS)(view):
        return "game"
    return kind_of(_SOFTWARE_KINDS, default="software")(view)


def _duration(view: EntityView) -> int:
    """P2047 duration as whole seconds (0 when absent or not a number)."""
    value = claim("P2047")(view)
    try:
        return int(float(value))
    except ValueError:
        return 0


PERSON_PLAN = Plan(
    model=Person,
    when=has_class("Q5"),
    key=lambda view: str(view.raw.get("id") or ""),
    fields={
        "name": person_name(),
        "gender": claim("P21", lang="en"),
        "birth_date": claim("P569"),
        "death_date": claim("P570"),
        "country": claim("P27", lang="en"),
        "occupations": claims("P106", lang="en"),
        "identifiers": ext_ids(
            P214="viaf",
            P213="isni",
            P227="gnd",
            P244="lccn",
            P496="orcid",
            P648="openlibrary",
        ),
        "extra": i18n_extra(person=True),
    },
)

SOFTWARE_PLAN = Plan(
    model=Software,
    when=lambda view: (
        has_class(*GAME_CLASSES, *SOFTWARE_CLASSES)(view)
        or has_claim(*_GAME_ID_PROPS, *_SOFTWARE_PROPS)(view)
    ),
    key=lambda view: str(view.raw.get("id") or ""),
    fields={
        "title": work_title(),
        "kind": _software_kind,
        "publication_date": first(claim("P577"), claim("P571")),
        "description": description(),
        "website": first(claim("P856"), claim("P1324")),
        "series": claims("P179"),
        "developers": claims("P178"),
        "publishers": claims("P123"),
        "platforms": claims("P400", "P306", lang="en"),
        "genres": claims("P136", lang="en"),
        "licenses": claims("P275", lang="en"),
        "identifiers": ext_ids(
            P1733="steam",
            P5794="igdb",
            P2725="gog",
            P11688="mobygames",
            P3180="vndb",
            P6337="pcgamingwiki",
            P2816="hltb",
            P7597="lutris",
            P4769="gamefaqs",
            P5290="redump",
            P6278="epic",
            P4655="flathub",
            P3454="arch",
            P3442="debian",
            P3597="fdroid",
            P3418="gplay",
            P4763="crates",
            P2209="sourceforge",
        ),
        "extra": i18n_extra(),
    },
)

TEXT_PLAN = Plan(
    model=Text,
    when=lambda view: has_class(*TEXT_CLASSES)(view) or has_claim(*_TEXT_ID_PROPS)(view),
    key=lambda view: str(view.raw.get("id") or ""),
    fields={
        "title": work_title(),
        "kind": kind_of(_TEXT_KINDS, props=("P7937", "P31"), default="text"),
        "publication_date": first(claim("P577"), claim("P571")),
        "lang": claim("P407", lang="en"),
        "description": description(),
        "series": claims("P179"),
        "publishers": claims("P123"),
        "genres": claims("P136", lang="en"),
        "identifiers": ext_ids(
            P212="isbn13",
            P957="isbn10",
            P236="issn",
            P356="doi",
            P214="viaf",
            P244="lccn",
            P648="openlibrary",
            P1085="librarything",
            P2969="goodreads",
        ),
        "extra": i18n_extra(),
    },
    links={
        "author_ids": LinkRef(item_ids("P50"), model=Person),
    },
)

VIDEO_PLAN = Plan(
    model=Video,
    when=lambda view: has_class(*VIDEO_CLASSES)(view) or has_claim(*_VIDEO_ID_PROPS)(view),
    key=lambda view: str(view.raw.get("id") or ""),
    fields={
        "title": work_title(),
        "kind": kind_of(_VIDEO_KINDS, default="video"),
        "publication_date": first(claim("P577"), claim("P571")),
        "lang": claim("P407", lang="en"),
        "description": description(),
        "duration": _duration,
        "series": claims("P179"),
        "directors": claims("P57", lang="en"),
        "publishers": claims("P272", lang="en"),
        "genres": claims("P136", lang="en"),
        "identifiers": ext_ids(
            P345="imdb",
            P4947="tmdb",
            P4983="tmdb-tv",
        ),
        "extra": i18n_extra(),
    },
)

SOUND_PLAN = Plan(
    model=Sound,
    when=lambda view: has_class(*SOUND_CLASSES)(view) or has_claim(*_SOUND_ID_PROPS)(view),
    key=lambda view: str(view.raw.get("id") or ""),
    fields={
        "title": work_title(),
        "kind": kind_of(_SOUND_KINDS, default="sound"),
        "publication_date": first(claim("P577"), claim("P571")),
        "lang": claim("P407", lang="en"),
        "description": description(),
        "duration": _duration,
        "series": claims("P179"),
        "performers": claims("P175", lang="en"),
        "publishers": claims("P264", lang="en"),
        "genres": claims("P136", lang="en"),
        "identifiers": ext_ids(
            P436="musicbrainz",
            P1243="isrc",
        ),
        "extra": i18n_extra(),
    },
)

PLANS: tuple[Plan, ...] = (
    PERSON_PLAN,
    SOFTWARE_PLAN,
    TEXT_PLAN,
    VIDEO_PLAN,
    SOUND_PLAN,
)
