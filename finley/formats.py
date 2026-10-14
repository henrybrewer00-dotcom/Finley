"""The cold email format library.

Every email Finley writes follows exactly one named format. A format is a spec, not a
template: the writer fills its parts from the lead's research, in the user's voice.

The library covers the whole sequence: first touches, follow-ups and a final email. Each
format carries the principle it is built on; docs/cold-email-playbook.md has the research
behind them. Add formats here, load more from a JSON file with `load_extra`, or mine them
from your own emails that got replies with `finley formats mine`. A new format should earn
its place by beating the current best on Email Bench and in a live A/B test.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

STEPS = ("first", "follow_up", "final")
# What a format can require. "fact": research found something specific; "trigger": a timely
# event; "proof": the campaign has a case study; "mutual": the lead has a real mutual connection.
NEEDS = ("fact", "trigger", "proof", "mutual")


@dataclass(frozen=True)
class Format:
    key: str
    name: str
    parts: tuple[str, ...]          # the email's beats, in order
    words: tuple[int, int]          # target body length (min, max)
    cta: str                        # the kind of ask
    use_when: str
    needs: tuple[str, ...] = ()     # see NEEDS
    example: str = ""
    step: str = "first"             # first | follow_up | final
    principle: str = ""             # why this shape gets replies

    def spec(self) -> str:
        beats = "\n".join(f"  {i}. {p}" for i, p in enumerate(self.parts, 1))
        why = f"Why it works: {self.principle}\n" if self.principle else ""
        return (f"Format: {self.name}\n{why}Beats:\n{beats}\nLength: {self.words[0]}-{self.words[1]} words.\n"
                f"Call to action: {self.cta}\nExample (for shape only, never copy its facts):\n{self.example}")


_ALL = [
    # ---- First touches -------------------------------------------------------------------
    Format(
        key="quick_question", name="Quick question",
        parts=("One line of context that shows you looked at them", "One direct question tied to the offer"),
        words=(20, 50), cta="A yes/no question", needs=("fact",),
        use_when="Little research beyond the basics; ultra-cold outreach.",
        principle="Short emails with one question are the easiest thing in an inbox to answer.",
        example="Hi Dana,\n\nNoticed Ridgeline runs three crews across Austin. Who books your estimates when everyone's on a job?\n\nSam",
    ),
    Format(
        key="trigger", name="Trigger",
        parts=("What we noticed (a specific, recent event)", "What that event usually means for a company like theirs",
               "How we help with exactly that", "A low-effort question"),
        words=(40, 90), cta="Interest check", needs=("trigger",),
        use_when="A fresh signal: hiring, funding, new location, launch, leadership change.",
        principle="A recent event explains why you're writing now, which makes the email feel earned.",
        example="Hi Marco,\n\nSaw you're hiring two dispatchers in Tampa. Usually that means the phones are outrunning the team.\n\nWe answer overflow calls and book them straight into your calendar, so new hires start on scheduled work instead of voicemail.\n\nWorth comparing notes before you fill both seats?\n\nSam",
    ),
    Format(
        key="timeline_hook", name="Timeline hook",
        parts=("The event and roughly when it happened", "What peers usually hit in the weeks after that event",
               "One line on how we help at that moment", "Interest check"),
        words=(40, 80), cta="Interest check", needs=("trigger",),
        use_when="A trigger with a clear clock on it: funding, new office opening, big hire, launch date.",
        principle="Tying the pain to a moment on their calendar beats naming a generic problem.",
        example="Hi Aisha,\n\nWith the Denver office opening this spring, the first month is usually when new-patient calls double and the front desk is still learning the phones.\n\nWe pick up the overflow and book it straight into your schedule.\n\nWorth having in place before opening day?\n\nSam",
    ),
    Format(
        key="new_in_seat", name="New in seat",
        parts=("Their new role (no congratulations)", "The decision people in that seat usually face early",
               "How we make that decision easier", "Soft ask"),
        words=(40, 80), cta="Interest check", needs=("trigger",),
        use_when="The trigger is that this person recently started or was promoted into the role.",
        principle="New leaders review tools and vendors in their first 90 days, so timing does the selling.",
        example="Hi Raj,\n\nYou stepped into the RevOps lead role last month. Most people in that seat get asked for a clean pipeline report within the first quarter.\n\nWe dedupe and tag the CRM so that report is trustworthy on day one.\n\nUseful to see how other teams did it?\n\nSam",
    ),
    Format(
        key="observation", name="Observation + curiosity",
        parts=("A specific observation about them from research", "A curious question about what that implies",
               "One line of proof or what we do", "Casual ask"),
        words=(35, 75), cta="Casual question", needs=("fact",),
        use_when="Research found a concrete detail (a service, a page, a location, a hiring post) but no trigger.",
        principle="An honest observation proves the email isn't a blast; the question invites a real answer.",
        example="Hi Leo,\n\nYour site says every estimate is free and same-day. Curious how that holds up in July when AC calls spike?\n\nWe book overflow calls for HVAC shops so same-day stays true.\n\nWorth a look?\n\nSam",
    ),
    Format(
        key="hypothesis", name="Hypothesis",
        parts=("A fact from research", "Our hunch about the problem it creates, stated as a guess",
               "What we'd do about it", "Ask if we're off"),
        words=(40, 80), cta="Ask them to correct you", needs=("fact",),
        use_when="We can reason from a fact to a likely pain but can't be sure.",
        principle="Admitting it's a guess lowers defenses, and people love to correct a wrong guess.",
        example="Hi Grace,\n\nYou run three locations with one shared front desk number. My guess is the busiest office answers and the other two go to voicemail at lunch.\n\nWe catch those overflow calls and book them.\n\nAm I off base?\n\nSam",
    ),
    Format(
        key="poke_the_bear", name="Poke the bear",
        parts=("A neutral question about how they handle something today",
               "What usually gets missed with that approach", "Ask whether it's worth a look"),
        words=(30, 70), cta="Neutral question", needs=(),
        use_when="A common, unspoken risk in the status quo and no strong trigger.",
        principle="A neutral question makes the reader examine their own process instead of hearing a pitch.",
        example="Hi Nina,\n\nWhen a new-patient call comes in while both front desk staff are on the phone, what happens to it?\n\nMost practices assume it goes to voicemail. Usually the caller hangs up and books elsewhere.\n\nWorth finding out how often it happens at yours?\n\nSam",
    ),
    Format(
        key="qvc", name="Question-Value-CTA",
        parts=("A question about a real problem they likely have", "One sentence of value", "One ask"),
        words=(25, 60), cta="Interest check", needs=(),
        use_when="Fully cold prospect, little research, and a sharp single value proposition.",
        principle="Three sentences, one idea: the reader gets it in five seconds.",
        example="Hi Omar,\n\nHow many after-hours calls does Delta Electric miss in a normal week?\n\nWe answer them live and book the job for $299 a month.\n\nOpen to trying it for a week?\n\nSam",
    ),
    Format(
        key="pas", name="Problem-Agitate-Solve",
        parts=("Name a problem common to their role/industry", "What it quietly costs them",
               "One sentence on how we fix it", "Soft ask"),
        words=(50, 100), cta="Interest check", needs=(),
        use_when="A clear, common pain for the persona and no strong trigger.",
        principle="Loss aversion: people move to stop a loss faster than to chase a gain.",
        example="Hi Priya,\n\nMonday mornings are usually when new-patient calls stack up on hold at a busy practice.\n\nThose callers rarely leave a voicemail; they book with the next practice on Google.\n\nWe pick up every overflow call and book it into Dentrix in under a minute.\n\nOpen to seeing how many Monday calls you're missing?\n\nSam",
    ),
    Format(
        key="bab", name="Before-After-Bridge",
        parts=("Their likely today (before)", "A real peer's result (after) with a number",
               "How we got that peer there (bridge)", "Ask"),
        words=(50, 100), cta="Offer to share how", needs=("proof",),
        use_when="We have a matching case study with a concrete number.",
        principle="A peer's before and after lets the reader picture the change without being told.",
        example="Hi Jordan,\n\nRight now your techs probably quote jobs on paper and chase approvals by text.\n\nNorthside Plumbing was doing the same; they now close 31% more quotes on site.\n\nThe difference was letting customers approve and pay from a link before the tech leaves.\n\nWant the 2-minute version of what they changed?\n\nSam",
    ),
    Format(
        key="rrr", name="Relevance-Reward-Request",
        parts=("Why we're writing to them specifically", "A useful insight they can use even if they never reply",
               "A soft request for a conversation"),
        words=(60, 110), cta="Conversation", needs=("fact",),
        use_when="Enterprise or consultative sales where the ask is a conversation.",
        principle="Giving a usable insight up front earns the right to ask for time.",
        example="Hi Elena,\n\nYou lead revenue ops at a 400-person company that just added a second product line, so pipeline reporting is about to get messy.\n\nOne thing that helps: tag every opportunity with product line at creation, not at close. Teams that backfill it later lose about a quarter of the history.\n\nHappy to share the field setup we use with similar teams if useful. Worth 15 minutes next week?\n\nSam",
    ),
    Format(
        key="peer_proof", name="Peer proof",
        parts=("A similar company they'd recognize", "That company's result", "An offer to share how"),
        words=(30, 70), cta="Offer to share how", needs=("proof",),
        use_when="A recognizable customer in the same niche or city.",
        principle="Social proof from a near peer answers 'does this work for people like me?'",
        example="Hi Chris,\n\nWe work with Lakeside Roofing a few towns over. They booked 46 extra inspections last month from calls that used to go to voicemail.\n\nHappy to show you how they set it up. Interested?\n\nSam",
    ),
    Format(
        key="loss_math", name="Missed-revenue math",
        parts=("A fact about their business", "Simple math on what a gap costs, using only numbers we were given",
               "How we close the gap", "Ask"),
        words=(45, 90), cta="Interest check", needs=("fact", "proof"),
        use_when="Research or proof gives real numbers we can multiply without inventing any.",
        principle="A concrete loss in their own terms is more persuasive than a feature.",
        example="Hi Tess,\n\nYou run two hygiene chairs five days a week. Brightside filled 38 open hygiene slots in a month once they stopped missing recall calls.\n\nIf even half that happened at your practice, it pays for itself in the first week.\n\nWorth a quick look at your call log?\n\nSam",
    ),
    Format(
        key="give_first", name="Give first",
        parts=("A specific, useful finding about their business (from research)", "Why it matters",
               "Offer the rest for free"),
        words=(50, 100), cta="Offer of a free resource", needs=("fact",),
        use_when="Research surfaced something we can genuinely help with (site, reviews, listings).",
        principle="Reciprocity: a real, free piece of help makes a reply feel like the polite thing to do.",
        example="Hi Leah,\n\nQuick heads-up: your booking page doesn't load on mobile Safari; the button sits under the cookie banner.\n\nAnyone booking from an iPhone hits that wall, so it may be costing you bookings today.\n\nI wrote up two other quick fixes I spotted. Want me to send them over?\n\nSam",
    ),
    Format(
        key="content_hook", name="Their own words",
        parts=("Something they published or said (post, talk, page) from research", "The tension it points to",
               "How we help with that tension", "Ask"),
        words=(40, 85), cta="Interest check", needs=("fact",),
        use_when="Research found something the person or company published: a post, a talk, a stated value.",
        principle="Quoting their priorities back to them shows real attention, not a template.",
        example="Hi Ben,\n\nYour careers page says every customer call gets answered by a human. That gets hard to keep once you're past three crews.\n\nWe're the humans who answer when your team can't, and we book the job.\n\nWorth a look before summer?\n\nSam",
    ),
    Format(
        key="switch", name="Switch story",
        parts=("The tool, vendor or approach research shows they use today", "Where that usually falls short for teams like theirs",
               "What we do differently, in one line", "Ask"),
        words=(40, 85), cta="Interest check", needs=("fact",),
        use_when="Research names a tool, vendor or method they use today that our offer replaces or improves.",
        principle="Naming the current setup makes the comparison concrete without trashing anyone.",
        example="Hi Kim,\n\nLooks like after-hours calls at Apex go to an answering service that takes messages.\n\nThe catch is a message isn't a booked job; most callers try the next company before you call back.\n\nWe book the job on the first call.\n\nWorth comparing?\n\nSam",
    ),
    Format(
        key="no_oriented", name="No-oriented question",
        parts=("One line on what we do for peers like them", "A no-oriented ask ('Would it be a bad idea to...?')"),
        words=(25, 55), cta="A question where 'no' means yes", needs=(),
        use_when="Senior or skeptical readers who resist being pitched.",
        principle="Saying 'no' feels safe, so people answer questions built to be answered with a no.",
        example="Hi Victor,\n\nWe help Series B teams fill senior backend roles in weeks, not months.\n\nWould it be a bad idea to send over two profiles that match your open staff engineer role?\n\nSam",
    ),
    Format(
        key="owner_direct", name="Owner-direct (local)",
        parts=("Their business by name and a local detail", "A plain, concrete offer", "An easy yes"),
        words=(30, 70), cta="An easy yes", needs=(),
        use_when="Local and SMB owners who read their own inbox.",
        principle="Owners reply to plain, neighborly emails that respect their time.",
        example="Hi Tom,\n\nTom's Heating & Air has great reviews around Phoenix, so I'd bet some of those 9pm calls are worth a lot.\n\nWe answer after-hours calls for HVAC shops and book the job.\n\nWant me to set up a free week so you can see what you're missing?\n\nSam",
    ),
    Format(
        key="mutual", name="Mutual connection",
        parts=("Name the mutual connection and the real link", "Why they thought of this person",
               "One line on what we do", "Easy ask"),
        words=(35, 75), cta="Interest check", needs=("mutual",),
        use_when="The lead data names a real mutual connection or referrer.",
        principle="A shared contact borrows trust the cold email doesn't have yet.",
        example="Hi Mia,\n\nJess Park at Lakeside Roofing said you're dealing with the same voicemail problem they had last year.\n\nWe answer their overflow calls now and book the jobs.\n\nWorth a quick chat?\n\nSam",
    ),
    Format(
        key="right_person", name="Right person?",
        parts=("One line on what we do", "Ask who owns this area"),
        words=(20, 50), cta="Referral to the right person", needs=(),
        use_when="Unsure whether this person is the decision maker.",
        principle="Asking for direction is a tiny favor that most people grant.",
        example="Hi Ana,\n\nWe help finance teams close the books two days faster. Are you the right person to talk to about month-end close, or is that someone else on your team?\n\nSam",
    ),
    # ---- Follow-ups ------------------------------------------------------------------------
    Format(
        key="bump_value", name="Bump with something new", step="follow_up",
        parts=("One new, useful detail not in the earlier email", "Tie it to their situation", "Same easy ask"),
        words=(25, 60), cta="Same ask, easier", needs=(),
        use_when="Second touch when the first email had a clear ask; add value instead of 'checking in'.",
        principle="Most replies come after a follow-up, but only if it gives a new reason to answer.",
        example="Hi Dana,\n\nOne more thing on the after-hours calls: most of them come in between 6 and 9pm, right after crews head home.\n\nWorth seeing what that looks like for Ridgeline?\n\nSam",
    ),
    Format(
        key="new_angle", name="New angle", step="follow_up",
        parts=("A different pain or benefit than the earlier email", "One line of how we help", "Ask"),
        words=(30, 70), cta="Interest check", needs=(),
        use_when="The earlier email led with one angle; try a different one before giving up.",
        principle="A no-reply often means the angle missed, not that the person isn't a fit.",
        example="Hi Marco,\n\nDifferent angle: when dispatch is slammed, follow-up calls on quotes are usually the first thing to slip.\n\nWe call those back the same day so quotes don't go cold.\n\nUseful for Bellini?\n\nSam",
    ),
    Format(
        key="proof_follow_up", name="Proof follow-up", step="follow_up",
        parts=("A peer's result with a number", "Why it's relevant to them", "Offer to share how"),
        words=(30, 70), cta="Offer to share how", needs=("proof",),
        use_when="A follow-up when we have a case study the earlier email didn't use.",
        principle="Proof answers the silent 'does this work?' that stops people replying.",
        example="Hi Chris,\n\nLakeside Roofing booked 46 extra inspections in their first month with us, all from calls that used to hit voicemail.\n\nSummit's in the same spot during storm season. Want the short version of their setup?\n\nSam",
    ),
    Format(
        key="short_bump", name="Short bump", step="follow_up",
        parts=("One or two lines that bring the earlier email back up", "A single easy question"),
        words=(10, 35), cta="Yes/no", needs=(),
        use_when="A light second touch, sent as a reply in the same thread.",
        principle="A tiny nudge in the same thread resurfaces the first email without new effort to read.",
        example="Hi Priya,\n\nFloating this back up. Is missed Monday calls something worth fixing this quarter, or not a priority?\n\nSam",
    ),
    # ---- Final -----------------------------------------------------------------------------
    Format(
        key="breakup", name="Breakup", step="final",
        parts=("Acknowledge you've reached out before", "Close the loop politely", "One last easy yes"),
        words=(20, 50), cta="An easy yes or a no",
        use_when="The final step of a sequence.",
        principle="Telling someone you'll stop gives them permission to answer, often with a yes.",
        example="Hi Ben,\n\nI haven't heard back, so I'll assume the timing isn't right and stop here.\n\nIf missed calls ever become a headache, just reply 'calls' and I'll send the two-minute version.\n\nSam",
    ),
    Format(
        key="referral_close", name="Who should I ask?", step="final",
        parts=("Acknowledge this is the last note", "Ask who else owns this, or whether to stop"),
        words=(20, 50), cta="Referral or a no",
        use_when="Final step when the person may not be the decision maker.",
        principle="A last, easy question gives them a way out that still moves you forward.",
        example="Hi Ana,\n\nLast note from me. If month-end close isn't yours, who would be the right person to ask? If it's just not a priority, a quick 'no' works too.\n\nSam",
    ),
]

FORMATS: dict[str, Format] = {f.key: f for f in _ALL}


def signals(research: dict | None, has_proof: bool, lead_extra: dict | None = None) -> set[str]:
    """Which NEEDS the research, campaign and lead data can satisfy."""
    research = research or {}
    have = set()
    if research.get("facts"):
        have.add("fact")
    if research.get("trigger"):
        have.add("trigger")
    if has_proof:
        have.add("proof")
    extra = {k.lower(): v for k, v in (lead_extra or {}).items()}
    if any(extra.get(k) for k in ("mutual", "mutual_connection", "referrer", "referred_by")):
        have.add("mutual")
    return have


def available(research: dict | None, has_proof: bool, step: str = "first",
              lead_extra: dict | None = None, exclude: set[str] = frozenset()) -> list[Format]:
    """Formats whose requirements the research and campaign can actually satisfy."""
    have = signals(research, has_proof, lead_extra)
    return [f for f in FORMATS.values()
            if f.step == step and set(f.needs) <= have and f.key not in exclude]


def load_extra(path: str | Path) -> list[str]:
    """Add formats from a JSON list of Format fields (e.g. ones mined from winning emails)."""
    keys = []
    for item in json.loads(Path(path).read_text()):
        fmt = from_dict(item)
        FORMATS[fmt.key] = fmt
        keys.append(fmt.key)
    return keys


def from_dict(item: dict) -> Format:
    item = {k: v for k, v in item.items() if k in Format.__dataclass_fields__}
    item["parts"] = tuple(item["parts"])
    item["words"] = tuple(item["words"])
    item["needs"] = tuple(n for n in item.get("needs", ()) if n in NEEDS)
    if item.get("step", "first") not in STEPS:
        item["step"] = "first"
    return Format(**item)


def as_dicts() -> list[dict]:
    return [asdict(f) for f in FORMATS.values()]


def _load_env_formats() -> None:
    import os

    for path in filter(None, os.environ.get("FINLEY_FORMATS_FILE", "").split(os.pathsep)):
        load_extra(path)


_load_env_formats()
