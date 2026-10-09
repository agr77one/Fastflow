"""Meeting intelligence (SPEC T46-T47, V76-V79): transcript parsing, sectioning, the
tolerant section parser, owner grounding, and the map-reduce pipeline with a fake model."""

from __future__ import annotations

import ffp_meeting_intel as MI
import pytest


@pytest.fixture(autouse=True)
def _tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(MI, "INTEL_PATH", tmp_path / "meeting_intel.jsonl")


def _quill(*turns: tuple[int, str, str]) -> str:
    """Quill's shape: '[Ns] Speaker:' on its own line, then the text."""
    return "\n".join(f"[{s}s] {who}:\n{text}" for s, who, text in turns)


# ---------- transcript parsing ----------------------------------------------------------

def test_parse_turns_reads_quill_turns_with_multiline_text():
    tx = "preamble ignored\n[0s] AG:\nHello there.\nSecond line.\n[83s] Bryson:\nHi."
    turns = MI.parse_turns(tx)
    assert [(t.start_s, t.speaker, t.text) for t in turns] == [
        (0, "AG", "Hello there. Second line."),
        (83, "Bryson", "Hi."),
    ]


def test_parse_turns_accepts_text_on_the_marker_line_and_drops_empty_turns():
    turns = MI.parse_turns("[5s] Speaker 1: inline text\n[9s] Speaker 2:\n\n[12s] Speaker 1:\nmore")
    assert [(t.speaker, t.text) for t in turns] == [("Speaker 1", "inline text"), ("Speaker 1", "more")]


@pytest.mark.parametrize("tx", ["", "null", "just some minutes text without markers"])
def test_parse_turns_without_markers_is_empty(tx):
    assert MI.parse_turns(tx) == []


def test_people_shares_self_and_mixed_channels():
    turns = MI.parse_turns(_quill(
        (0, "Microphone (AG)", "x" * 10),
        (5, "AG", "x" * 10),
        (9, "Speaker 1", "x" * 60),
        (20, "Headphones (other speakers)", "x" * 20),
    ))
    ppl = {p["label"]: p for p in MI.people(turns)}
    assert MI.people(turns)[0]["label"] == "Speaker 1"            # most first
    assert ppl["Speaker 1"]["share_pct"] == 60
    assert ppl["Microphone (AG)"]["is_self"] and ppl["AG"]["is_self"]
    assert not ppl["Speaker 1"]["is_self"]
    assert ppl["Headphones (other speakers)"]["is_mixed"]


def test_sections_cut_at_turn_boundaries_and_keep_every_word():
    turns = [MI.Turn(i * 10, f"S{i % 3}", f"word{i:03d} " * 40) for i in range(30)]
    parts = MI.sections(turns, max_chars=1500)
    assert len(parts) > 3
    assert all(len(MI.render_section(p)) <= 1500 + 60 for p in parts)
    flat = [t for p in parts for t in p]
    assert [t.start_s for t in flat] == [t.start_s for t in turns]   # order kept, none dropped


def test_a_turn_longer_than_a_section_is_split_on_whitespace():
    giant = MI.Turn(42, "Speaker 1", " ".join(f"w{i}" for i in range(2000)))
    parts = MI.sections([MI.Turn(0, "AG", "intro"), giant, MI.Turn(900, "AG", "outro")], max_chars=1000)
    words = " ".join(t.text for p in parts for t in p).split()
    assert words == ["intro"] + [f"w{i}" for i in range(2000)] + ["outro"]
    assert all(t.start_s == 42 for p in parts for t in p if t.speaker == "Speaker 1")


# ---------- one section's reply -------------------------------------------------------------

def test_section_reply_parser_tolerates_bullets_bold_thinking_and_filler():
    reply = (
        "<think>let me see</think>\n"
        "- **TOPIC:** Pricing | Team debates a 10% raise.\n"
        "1. DECISION: Raise prices in October\n"
        "ACTION: Draft the announcement | Dana | Friday\n"
        "ACTION: None stated in this section.\n"
        "DECISION: - | -\n"
        "QUESTION: N/A\n"
        "random prose the model added\n"
    )
    out = MI.parse_section_reply(reply)
    assert out["TOPIC"] == [["Pricing", "Team debates a 10% raise."]]
    assert out["DECISION"] == [["Raise prices in October"]]
    assert out["ACTION"] == [["Draft the announcement", "Dana", "Friday"]]
    assert out["QUESTION"] == []


def test_section_prompt_has_no_none_escape_hatch():
    # With "say NONE if nothing", 8 of 10 real sections came back NONE (bake-off).
    assert "NONE if" not in MI.SECTION_SYSTEM
    assert "at least one TOPIC" in MI.SECTION_SYSTEM


# ---------- owners (V78) ---------------------------------------------------------------

def test_owner_grounding():
    labels = {"AG", "Speaker 1"}
    low = "then ag said dana would draft it"
    assert MI._owner("Speaker 1", labels, low) == ("Speaker 1", True)
    assert MI._owner("AG", labels, low) == ("AG", True)                 # two-letter label counts
    assert MI._owner("Dana", labels, low) == ("Dana", True)              # named in the transcript
    assert MI._owner("Joseph", labels, low) == ("Joseph", False)         # kept, but unconfirmed
    assert MI._owner("NONE", labels, low) == (None, False)
    assert MI._owner("", labels, low) == (None, False)


def test_topics_differing_only_by_a_number_are_not_merged():
    assert not MI._similar("Q3 pricing", "Q4 pricing", 0.5)
    assert MI._similar("Pricing update", "pricing update", 0.5)


# ---------- header / clusters / category -------------------------------------------------

def test_parse_clusters_assigns_each_topic_once_and_collects_leftovers():
    reply = "GROUP: Delivery | 1, 2 | work\nGROUP: Chat | 2, 4 | social\nGROUP: Bad | 99\n"
    themes = MI.parse_clusters(reply, 5)
    assert themes[0] == {"label": "Delivery", "topics": [0, 1], "low_value": False}
    assert themes[1] == {"label": "Chat", "topics": [3], "low_value": True}   # 2 already used
    assert themes[-1] == {"label": "Other", "topics": [2, 4], "low_value": False}


def test_parse_header():
    assert MI.parse_header("**TITLE:** Q4 Roadmap\nCATEGORY: Planning.") == ("Q4 Roadmap", "planning")


def test_two_real_speakers_is_a_one_to_one_whatever_the_model_says():
    two = [{"label": "AG", "is_mixed": False}, {"label": "Bryson", "is_mixed": False}]
    assert MI.derive_category(two, "standup") == "1:1"
    many = two + [{"label": "Speaker 3", "is_mixed": False}]
    assert MI.derive_category(many, "planning") == "planning"
    assert MI.derive_category(many, "a fun chat") == "other"


# ---------- the pipeline ---------------------------------------------------------------

class FakeLLM:
    """Scripted model: section calls get topic lines; header/cluster calls answered."""

    model = "fake:9b"

    def __init__(self, fail_sections=(), garbage_sections=()):
        self.calls = []
        self.fail = set(fail_sections)
        self.garbage = set(garbage_sections)

    def run(self, messages, max_tokens):
        system = messages[0]["content"]
        self.calls.append((system[:20], max_tokens))
        if system.startswith("Reply with exactly two lines"):
            return "TITLE: Weekly delivery sync\nCATEGORY: planning"
        if system.startswith("Group the numbered"):
            return "GROUP: Delivery | 1, 2, 3 | work\nGROUP: Weekend | 4 | social"
        n = sum(1 for s, _ in self.calls if s.startswith("You read ONE section"))
        if n in self.fail:
            raise RuntimeError("model hiccup")
        if n in self.garbage:
            return "I cannot help with that."
        return (f"TOPIC: Topic {n} | Gist of section {n}.\n"
                f"DECISION: Decision {n} was made\n"
                f"ACTION: Task {n} | {'Speaker 1' if n % 2 else 'Zed'}\n"
                f"QUESTION: Open question {n}?")


def _meeting_transcript(n_sections=4):
    turns = []
    for i in range(n_sections):
        turns.append((i * 300, "Speaker 1" if i % 2 else "Speaker 2", f"chunk{i} " * 380))
        turns.append((i * 300 + 60, "Microphone (AG)", "ok " * 20))
    return _quill(*turns)


def test_build_intel_maps_every_section_and_reduces_in_code():
    llm = FakeLLM()
    tx = _meeting_transcript(4)
    rec = MI.build_intel({"id": "m1", "title": "Quill title", "date": "2026-10-01"}, tx, llm)

    assert rec["schema"] == MI.SCHEMA and rec["meeting_id"] == "m1" and rec["model"] == "fake:9b"
    assert rec["sections"]["failed"] == 0 and rec["sections"]["ok"] == rec["sections"]["total"] >= 4
    assert rec["title"] == "Weekly delivery sync" and rec["category"] == "planning"
    assert [t["label"] for t in rec["topics"]][:2] == ["Topic 1", "Topic 2"]
    assert {t["theme"] for t in rec["topics"][:3]} == {"Delivery"}
    assert rec["topics"][3]["low_value"] is True                       # the "social" theme
    assert all(0 <= t["start_s"] <= rec["length_s"] for t in rec["topics"])   # V79
    owners = {a["owner"]: a["owner_confirmed"] for a in rec["actions"]}
    assert owners["Speaker 1"] is True and owners["Zed"] is False      # V78
    assert {p["label"] for p in rec["people"]} == {"Speaker 1", "Speaker 2", "Microphone (AG)"}


def test_a_failed_or_garbage_section_is_counted_not_fatal():
    rec = MI.build_intel({"id": "m2"}, _meeting_transcript(4), FakeLLM(fail_sections={2}, garbage_sections={3}))
    assert rec["sections"]["failed"] == 2                                # V77
    assert rec["sections"]["ok"] == rec["sections"]["total"] - 2
    assert "Topic 1" in [t["label"] for t in rec["topics"]]


def test_every_section_failing_raises():
    llm = FakeLLM(fail_sections=set(range(1, 50)))
    with pytest.raises(RuntimeError, match="every section failed"):
        MI.build_intel({"id": "m3"}, _meeting_transcript(3), llm)


@pytest.mark.parametrize("tx", ["", "null", "[0s] AG:\nThank you."])
def test_stub_transcripts_are_skipped_without_a_model_call(tx):
    llm = FakeLLM()
    with pytest.raises(MI.IntelSkip):
        MI.build_intel({"id": "m4"}, tx, llm)
    assert llm.calls == []


def test_few_topics_skip_the_clustering_call():
    llm = FakeLLM()
    rec = MI.build_intel({"id": "m5"}, _meeting_transcript(1), llm)
    assert rec["themes"] == []
    assert not any(s.startswith("Group the") for s, _ in llm.calls)


# ---------- store (V76: own file) --------------------------------------------------------

def test_store_roundtrip_upserts_one_row_per_meeting():
    a = {"schema": MI.SCHEMA, "meeting_id": "a", "date": "2026-10-01", "built_at": "1", "title": "A1",
         "people": [], "topics": [{"label": "x"}], "themes": [], "actions": []}
    MI.save_intel(a)
    MI.save_intel({**a, "built_at": "2", "title": "A2"})
    MI.save_intel({**a, "meeting_id": "b", "title": "B"})
    rows = MI.load_intel()
    assert set(rows) == {"a", "b"} and rows["a"]["title"] == "A2"
    assert MI.get_intel("a")["title"] == "A2" and MI.get_intel("zzz") is None
    assert len(MI.INTEL_PATH.read_text(encoding="utf-8").splitlines()) == 2
    facets = {f["meeting_id"]: f for f in MI.summaries()}
    assert facets["b"]["themes"] == ["x"]


def test_rows_from_another_schema_are_ignored():
    MI.INTEL_PATH.write_text('{"schema": 99, "meeting_id": "old"}\nnot json\n', encoding="utf-8")
    assert MI.load_intel() == {}


# ---------- seen on real meetings (2026-10-09 live run) -------------------------------------

@pytest.mark.parametrize("text", [
    "None explicitly stated in this section.", "None agreed or assigned in this section.",
    "None stated.", "No clear action items.", "No decisions were made.", "Nothing to report", "N/A", "-",
])
def test_filler_phrasings_are_dropped(text):
    assert MI._is_filler(text)


@pytest.mark.parametrize("text", [
    "Noel will send the deck", "Nonetheless we ship Friday", "No-code tool evaluation",
    "Notify the client by Friday", "No one objected to the Q3 plan", "Review the section 3 budget",
])
def test_real_items_that_look_like_filler_are_kept(text):
    assert not MI._is_filler(text)


def test_the_prompt_example_never_reaches_a_record():
    # An earlier example's question showed up in two live meetings, verbatim and then
    # paraphrased: echoes are dropped by content-word overlap, not exact text.
    class Echo(FakeLLM):
        def run(self, messages, max_tokens):
            out = super().run(messages, max_tokens)
            if messages[0]["content"].startswith("You read ONE section"):
                out += "\n".join([
                    "",
                    "QUESTION: Can the flour delivery arrive before 7 am?",     # verbatim
                    "QUESTION: Could the flour delivery come before 7am?",      # paraphrase
                    "ACTION: Order another proofing rack | Priya",
                    "TOPIC: Bake times | The bakers agree to start the sourdough before 6 am.",
                ])
            return out

    rec = MI.build_intel({"id": "e"}, _meeting_transcript(3), Echo())
    texts = [x["text"] for k in ("decisions", "actions", "questions") for x in rec[k]]
    texts += [t["label"] for t in rec["topics"]] + [t["gist"] for t in rec["topics"]]
    assert not any(w in t.lower() for t in texts for w in ("flour", "proofing", "sourdough"))
    assert any(t.startswith("Open question") for t in texts)          # real items survive


@pytest.mark.parametrize("text", [
    "Schedule the oven maintenance",                 # shares only the 2 words of a 2-word example line
    "Can the vendor deliver the hardware before Q4?",
    "Create a detailed finish-by-date schedule",
    "Raise prices for enterprise customers",
])
def test_real_items_are_not_mistaken_for_example_echoes(text):
    assert not MI._echoes_example(text, MI._example_texts())


def test_the_example_is_from_an_unrelated_domain():
    assert "bakery" in MI.SECTION_EXAMPLE and "never repeat its content" in MI.SECTION_EXAMPLE


# ---------- grouping leftovers (live: 3 of 15 topics left out of every group) ---------------

def test_groups_past_the_limit_fold_into_other_instead_of_vanishing():
    reply = "\n".join(f"GROUP: G{n} | {n}" for n in range(1, 9))     # 8 groups, limit 6
    themes = MI.parse_clusters(reply, 8)
    assert [t["label"] for t in themes] == ["G1", "G2", "G3", "G4", "G5", "G6", "Other"]
    assert themes[-1]["topics"] == [6, 7]
    assert sorted(i for t in themes for i in t["topics"]) == list(range(8))


class _Assigner:
    model = "fake"

    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def run(self, messages, max_tokens):
        self.calls.append(messages[1]["content"])
        return self.reply


def test_leftover_topics_are_placed_in_the_existing_themes():
    topics = [{"label": f"t{i}"} for i in range(6)]
    themes = [{"label": "Delivery", "topics": [0, 1], "low_value": False},
              {"label": "Chat", "topics": [2], "low_value": True},
              {"label": "Other", "topics": [3, 4, 5], "low_value": False}]
    llm = _Assigner("4 | Delivery\n5 | **chat**\n6 | Nonsense theme\n4 | Chat")
    out = MI.assign_leftovers(themes, topics, llm)
    assert out == [{"label": "Delivery", "topics": [0, 1, 3], "low_value": False},
                   {"label": "Chat", "topics": [2, 4], "low_value": True},
                   {"label": "Other", "topics": [5], "low_value": False}]
    assert "4. t3" in llm.calls[0] and "- Delivery" in llm.calls[0]


def test_build_intel_runs_the_leftover_pass_only_when_needed():
    class Partial(FakeLLM):
        def run(self, messages, max_tokens):
            if messages[0]["content"].startswith("Group the numbered"):
                self.calls.append(("Group", max_tokens))
                return "GROUP: Delivery | 1, 2 | work"                   # leaves topics 3+ out
            if messages[0]["content"].startswith("Assign each"):
                self.calls.append(("Assign", max_tokens))
                return "\n".join(f"{n} | Delivery" for n in range(3, 10))
            return super().run(messages, max_tokens)

    rec = MI.build_intel({"id": "p"}, _meeting_transcript(4), Partial())
    assert [t["label"] for t in rec["themes"]] == ["Delivery"]
    assert all(t.get("theme") == "Delivery" for t in rec["topics"])

    llm = FakeLLM()                                                     # groups everything itself
    MI.build_intel({"id": "q"}, _meeting_transcript(4), llm)
    assert not any(s.startswith("Assign") for s, _ in llm.calls)
