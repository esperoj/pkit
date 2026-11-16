"""serialize: the record <-> JSON boundary (public to_dict/to_json/from_*)."""

import pytest

from pkit.meta.models import Person, Video
from pkit.meta.serialize import from_dict, from_json, to_dict, to_json


def test_to_dict_and_to_json_round_trip():
    person = Person(name="Jun", occupations=["writer"])

    assert to_dict(person)["name"] == "Jun"
    assert from_json(to_json(person)) == person


@pytest.mark.parametrize(
    "payload",
    [{}, {"type": ""}, {"type": 42}, {"type": "alien"}],
    ids=["no-type", "empty-type", "non-str-type", "unknown-type"],
)
def test_from_dict_rejects_bad_type(payload):
    with pytest.raises(ValueError):
        from_dict(payload)


def test_from_dict_ignores_unknown_keys():
    record = from_dict({"type": "video", "id": "v1", "duration": 4800, "future": "field"})

    assert isinstance(record, Video)
    assert record.duration == 4800
