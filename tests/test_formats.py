import json

from finley import formats
from finley.formats import FORMATS, available


def test_library_covers_the_whole_sequence_with_complete_specs():
    assert len(FORMATS) >= 25
    steps = {f.step for f in FORMATS.values()}
    assert steps == {"first", "follow_up", "final"}
    for f in FORMATS.values():
        assert f.parts and f.example and f.principle and f.words[0] < f.words[1]
        assert f.name in f.spec()
        assert set(f.needs) <= set(formats.NEEDS)


def test_every_example_passes_the_style_gates():
    """The examples teach the writer the shape, so they must follow our own rules."""
    from finley import gates
    for f in FORMATS.values():
        r = gates.check({"subject": "after-hours calls", "body": f.example, "claims": []}, f, {}, {})
        assert r.passed, (f.key, r.failures)


def test_available_respects_requirements():
    keys = lambda fs: {f.key for f in fs}
    bare = keys(available({}, has_proof=False))
    assert "trigger" not in bare and "bab" not in bare and "quick_question" not in bare
    assert {"pas", "owner_direct", "right_person"} <= bare
    rich = keys(available({"facts": [{"id": "f1"}], "trigger": "hiring"}, has_proof=True))
    assert {"trigger", "bab", "peer_proof", "quick_question", "give_first"} <= rich
    assert "breakup" not in rich
    assert keys(available({}, False, step="final")) == {"breakup", "referral_close"}
    assert "mutual" not in rich
    assert "mutual" in keys(available({}, False, lead_extra={"mutual_connection": "Jess"}))
    assert "pas" not in keys(available({}, False, exclude={"pas"}))


def test_load_extra(tmp_path):
    path = tmp_path / "extra.json"
    path.write_text(json.dumps([{"key": "two_liner", "name": "Two-liner",
                                 "parts": ["Name the result", "Ask"], "words": [20, 50],
                                 "cta": "Intro", "use_when": "Short proof", "needs": ["proof", "bogus"],
                                 "unknown_field": 1}]))
    assert formats.load_extra(path) == ["two_liner"]
    try:
        assert FORMATS["two_liner"].parts == ("Name the result", "Ask")
        assert FORMATS["two_liner"].needs == ("proof",)
    finally:
        FORMATS.pop("two_liner")


def test_fact_kinds_unlock_formats_precisely():
    keys = lambda r: {f.key for f in available(r, has_proof=False)}
    service_only = keys({"facts": [{"id": "f1", "kind": "service"}]})
    assert "switch" not in service_only and "content_hook" not in service_only and "observation" in service_only
    tooled = keys({"facts": [{"id": "f1", "kind": "tool"}, {"id": "f2", "kind": "published"}]})
    assert {"switch", "content_hook"} <= tooled and "new_in_seat" not in tooled
    assert {"new_in_seat", "trigger"} <= keys({"facts": [{"id": "f1", "kind": "new_role"}]})
    # Research saved before facts had kinds keeps the old behavior.
    assert {"switch", "content_hook"} <= keys({"facts": [{"id": "f1"}]})
