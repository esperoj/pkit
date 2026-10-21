from dataclasses import dataclass

type Entity = Agent | Book

# example: https://www.wikidata.org/wiki/Q501514
@dataclass(frozen=True, slots=True)
class Book():
    title: str
    author: str
    publisher: str
    date: str
    identifier: str

@dataclass(frozen=True, slots=True)
class Agent():
    name: str
    born: str
    identifier: str

