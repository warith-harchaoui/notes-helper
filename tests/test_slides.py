"""Unit tests for :mod:`notes_helper.slides` alignment (no PDF, no OCR, no models).

The point of the feature is that the slide shown follows *content*, not slide order, so a
meeting that jumps back to an earlier slide jumps back with it. These tests pin exactly
that behaviour on synthetic decks and transcripts.
"""

from __future__ import annotations

from notes_helper.slides import align_slides


# A tiny three-slide deck with clearly distinct vocabulary per slide.
SLIDES = [
    "Introduction agenda objectives welcome team",
    "Budget revenue costs margin forecast finance",
    "Roadmap milestones delivery timeline release",
]


def test_align_follows_content_in_order() -> None:
    """When the talk walks the deck in order, the timeline follows it in order."""
    transcript = [
        {"t0": 0.0, "t1": 5.0, "text": "welcome team, here is today's agenda and objectives"},
        {"t0": 5.0, "t1": 10.0, "text": "let's look at the budget: revenue, costs and margin"},
        {"t0": 10.0, "t1": 15.0, "text": "now the roadmap, milestones and delivery timeline"},
    ]
    spans = align_slides(transcript, SLIDES)
    assert [s["slide"] for s in spans] == [0, 1, 2]


def test_align_handles_back_and_forth() -> None:
    """A jump back to slide 1's material must select slide 1 again — not stay chronological."""
    transcript = [
        {"t0": 0.0, "t1": 5.0, "text": "welcome team, the agenda and objectives for today"},
        {"t0": 5.0, "t1": 10.0, "text": "roadmap milestones and the release timeline first"},
        {"t0": 10.0, "t1": 15.0, "text": "wait, back to the budget — revenue, costs, margin, forecast"},
    ]
    spans = align_slides(transcript, SLIDES)
    slides = [s["slide"] for s in spans]
    # Order-free: intro (0) → roadmap (2) → back to budget (1).
    assert slides == [0, 2, 1]


def test_align_carries_previous_on_weak_match() -> None:
    """Filler speech with no slide signal keeps the current slide instead of flickering."""
    transcript = [
        {"t0": 0.0, "t1": 5.0, "text": "budget revenue costs margin forecast finance"},
        {"t0": 5.0, "t1": 8.0, "text": "uh, yeah, hmm, okay so"},  # no slide vocabulary
        {"t0": 8.0, "t1": 12.0, "text": "roadmap milestones delivery timeline release"},
    ]
    spans = align_slides(transcript, SLIDES)
    # The filler span merges into slide 1 (carried forward), then we move to slide 2.
    assert [s["slide"] for s in spans] == [1, 2]


def test_align_merges_consecutive_same_slide_spans() -> None:
    """Consecutive utterances on the same slide collapse into a single covering span."""
    transcript = [
        {"t0": 0.0, "t1": 4.0, "text": "budget revenue costs"},
        {"t0": 4.0, "t1": 9.0, "text": "margin forecast finance"},
    ]
    spans = align_slides(transcript, SLIDES)
    assert len(spans) == 1
    assert spans[0]["t0"] == 0.0 and spans[0]["t1"] == 9.0
    assert spans[0]["slide"] == 1


def test_align_no_slides_is_empty() -> None:
    """No deck → no timeline (the feature is simply absent, not an error)."""
    assert align_slides([{"t0": 0.0, "t1": 1.0, "text": "hello"}], []) == []


def test_align_extreme_out_of_order_jumps() -> None:
    """A 26-slide deck visited far out of order (0→14→7→2→25) maps exactly — the
    matcher has no monotonicity assumption, so arbitrary jumps just work."""
    # Each slide carries its own unique, unambiguous vocabulary.
    deck = [f"slidetopic{i} keyword{i} concept{i}" for i in range(26)]
    visited = [0, 14, 7, 2, 25]
    transcript = [
        {"t0": float(k * 5), "t1": float(k * 5 + 5), "text": f"let's discuss slidetopic{i} and keyword{i}"}
        for k, i in enumerate(visited)
    ]
    spans = align_slides(transcript, deck)
    assert [s["slide"] for s in spans] == visited
