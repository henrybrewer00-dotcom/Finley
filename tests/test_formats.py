import json

from finley import formats
from finley.formats import FORMATS, available


def test_ten_seed_formats_with_complete_specs():
    assert len(FORMATS) >= 10
    for f in FORMATS.values():
        assert f.parts and f.example and f.words[0] < f.words[1]
        assert f.name in f.spec()


def test_available_respects_requirements():
    keys = lambda fs: {f.key for f in fs}
    bare = keys(available({}, has_proof=False))
    assert "trigger" not in bare and "bab" not in bare and "quick_question" not in bare
    assert {"pas", "owner_direct", "right_person"} <= bare
    rich = keys(available({"facts": [{"id": "f1"}], "trigger": "hiring"}, has_proof=True))
    assert {"trigger", "bab", "peer_proof", "quick_question", "give_first"} <= rich
    assert "breakup" not in rich
    assert keys(available({}, False, step="final")) == {"breakup"}


def test_load_extra(tmp_path):
    path = tmp_path / "extra.json"
    path.write_text(json.dumps([{"key": "mutual", "name": "Mutual connection",
                                 "parts": ["Name the mutual", "Ask"], "words": [20, 50],
                                 "cta": "Intro", "use_when": "Shared contact"}]))
    formats.load_extra(path)
    try:
        assert FORMATS["mutual"].parts == ("Name the mutual", "Ask")
    finally:
        FORMATS.pop("mutual")
