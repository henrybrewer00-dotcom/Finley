"""The cold email format library.

Every email Finley writes follows exactly one named format. A format is a spec, not a
template: the writer fills its parts from the lead's research, in the user's voice.

Add formats here (or load more from a JSON file with `load_extra`). A new format should earn
its place by beating the current best on Email Bench and in a live A/B test.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Format:
    key: str
    name: str
    parts: tuple[str, ...]          # the email's beats, in order
    words: tuple[int, int]          # target body length (min, max)
    cta: str                        # the kind of ask
    use_when: str
    needs: tuple[str, ...] = ()     # what research must contain: "trigger", "proof", "fact"
    example: str = ""
    step: str = "first"             # first | follow_up | final

    def spec(self) -> str:
        beats = "\n".join(f"  {i}. {p}" for i, p in enumerate(self.parts, 1))
        return (f"Format: {self.name}\nBeats:\n{beats}\nLength: {self.words[0]}-{self.words[1]} words.\n"
                f"Call to action: {self.cta}\nExample (for shape only, never copy its facts):\n{self.example}")


FORMATS: dict[str, Format] = {f.key: f for f in [
    Format(
        key="quick_question", name="Quick question",
        parts=("One line of context that shows you looked at them", "One direct question tied to the offer"),
        words=(20, 50), cta="A yes/no question", needs=("fact",),
        use_when="Little research beyond the basics; ultra-cold outreach.",
        example="Hi Dana,\n\nNoticed Ridgeline runs three crews across Austin. Who books your estimates when everyone's on a job?\n\nSam",
    ),
    Format(
        key="trigger", name="Trigger",
        parts=("What we noticed (a specific, recent event)", "What that event usually means for a company like theirs",
               "How we help with exactly that", "A low-effort question"),
        words=(40, 90), cta="Interest check", needs=("trigger",),
        use_when="A fresh signal: hiring, funding, new location, launch, leadership change.",
        example="Hi Marco,\n\nSaw you're hiring two dispatchers in Tampa. Usually that means the phones are outrunning the team.\n\nWe answer overflow calls and book them straight into your calendar, so new hires start on scheduled work instead of voicemail.\n\nWorth comparing notes before you fill both seats?\n\nSam",
    ),
    Format(
        key="pas", name="Problem-Agitate-Solve",
        parts=("Name a problem common to their role/industry", "What it quietly costs them",
               "One sentence on how we fix it", "Soft ask"),
        words=(50, 100), cta="Interest check", needs=(),
        use_when="A clear, common pain for the persona and no strong trigger.",
        example="Hi Priya,\n\nMonday mornings are usually when new-patient calls stack up on hold at a busy practice.\n\nThose callers rarely leave a voicemail; they book with the next practice on Google.\n\nWe pick up every overflow call and book it into Dentrix in under a minute.\n\nOpen to seeing how many Monday calls you're missing?\n\nSam",
    ),
    Format(
        key="bab", name="Before-After-Bridge",
        parts=("Their likely today (before)", "A real peer's result (after) with a number",
               "How we got that peer there (bridge)", "Ask"),
        words=(50, 100), cta="Offer to share how", needs=("proof",),
        use_when="We have a matching case study with a concrete number.",
        example="Hi Jordan,\n\nRight now your techs probably quote jobs on paper and chase approvals by text.\n\nNorthside Plumbing was doing the same; they now close 31% more quotes on site.\n\nThe difference was letting customers approve and pay from a link before the tech leaves.\n\nWant the 2-minute version of what they changed?\n\nSam",
    ),
    Format(
        key="rrr", name="Relevance-Reward-Request",
        parts=("Why we're writing to them specifically", "A useful insight they can use even if they never reply",
               "A soft request for a conversation"),
        words=(60, 110), cta="Conversation", needs=("fact",),
        use_when="Enterprise or consultative sales where the ask is a conversation.",
        example="Hi Elena,\n\nYou lead revenue ops at a 400-person company that just added a second product line, so pipeline reporting is about to get messy.\n\nOne thing that helps: tag every opportunity with product line at creation, not at close. Teams that backfill it later lose about a quarter of the history.\n\nHappy to share the field setup we use with similar teams if useful. Worth 15 minutes next week?\n\nSam",
    ),
    Format(
        key="peer_proof", name="Peer proof",
        parts=("A similar company they'd recognize", "That company's result", "An offer to share how"),
        words=(30, 70), cta="Offer to share how", needs=("proof",),
        use_when="A recognizable customer in the same niche or city.",
        example="Hi Chris,\n\nWe work with Lakeside Roofing a few towns over. They booked 46 extra inspections last month from calls that used to go to voicemail.\n\nHappy to show you how they set it up. Interested?\n\nSam",
    ),
    Format(
        key="give_first", name="Give first",
        parts=("A specific, useful finding about their business (from research)", "Why it matters",
               "Offer the rest for free"),
        words=(50, 100), cta="Offer of a free resource", needs=("fact",),
        use_when="Research surfaced something we can genuinely help with (site, reviews, listings).",
        example="Hi Leah,\n\nQuick heads-up: your booking page doesn't load on mobile Safari; the button sits under the cookie banner.\n\nAnyone booking from an iPhone hits that wall, so it may be costing you bookings today.\n\nI wrote up two other quick fixes I spotted. Want me to send them over?\n\nSam",
    ),
    Format(
        key="owner_direct", name="Owner-direct (local)",
        parts=("Their business by name and a local detail", "A plain, concrete offer", "An easy yes"),
        words=(30, 70), cta="An easy yes", needs=(),
        use_when="Local and SMB owners who read their own inbox.",
        example="Hi Tom,\n\nI help HVAC shops around Phoenix answer every after-hours call. Tom's Heating & Air has great reviews, so I'd bet some of those 9pm calls are worth a lot.\n\nWant me to set up a free week so you can see what you're missing?\n\nSam",
    ),
    Format(
        key="right_person", name="Right person?",
        parts=("One line on what we do", "Ask who owns this area"),
        words=(20, 50), cta="Referral to the right person", needs=(), step="first",
        use_when="Unsure whether this person is the decision maker.",
        example="Hi Ana,\n\nWe help finance teams close the books two days faster. Are you the right person to talk to about month-end close, or is that someone else on your team?\n\nSam",
    ),
    Format(
        key="breakup", name="Breakup",
        parts=("Acknowledge you've reached out before", "Close the loop politely", "One last easy yes"),
        words=(20, 50), cta="An easy yes or a no", needs=(), step="final",
        use_when="The final step of a sequence.",
        example="Hi Ben,\n\nI haven't heard back, so I'll assume the timing isn't right and stop here.\n\nIf missed calls ever become a headache, just reply 'calls' and I'll send the two-minute version.\n\nSam",
    ),
]}


def available(research: dict | None, has_proof: bool, step: str = "first") -> list[Format]:
    """Formats whose requirements the research and campaign can actually satisfy."""
    research = research or {}
    have = set()
    if research.get("facts"):
        have.add("fact")
    if research.get("trigger"):
        have.add("trigger")
    if has_proof:
        have.add("proof")
    return [f for f in FORMATS.values() if f.step == step and set(f.needs) <= have]


def load_extra(path: str | Path) -> None:
    """Add formats from a JSON list of Format fields (e.g. ones mined from winning emails)."""
    for item in json.loads(Path(path).read_text()):
        item["parts"] = tuple(item["parts"])
        item["words"] = tuple(item["words"])
        item["needs"] = tuple(item.get("needs", ()))
        FORMATS[item["key"]] = Format(**item)


def as_dicts() -> list[dict]:
    return [asdict(f) for f in FORMATS.values()]
