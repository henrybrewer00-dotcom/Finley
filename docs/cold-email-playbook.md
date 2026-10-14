# Cold email playbook

What the research says gets cold emails answered, and how Finley turns each finding into a writer
rule, a format or an automatic check. When a rule here changes, change the code with it and rerun
`finley bench` to see whether the change helped.

Most public numbers come from vendors measuring their own platforms, so treat them as direction,
not law. Finley's own reply data (by format and score band, on the dashboard) is the real test.

## What the data says

| Finding | Evidence | How Finley applies it |
|---|---|---|
| Short wins. Replies peak around 50-75 words, three to five sentences, two or three paragraphs. | Reply.io platform data; a 64,562-email analysis cited by Prospeo found 70 words or fewer did best; one sender went from 3% to 6% replies after cutting 141 words to 56 (other things changed too). | Each format has a word range, most within 25-90. The gate fails anything 25% outside it. The writer is told to delete every sentence the email works without. |
| Subject lines of 1-4 words get the most opens. Opens fall steadily after that. | Belkins, 5.5M emails (2-4 words: 46% opens, 7 words: 39%, 10 words: 34%); 30 Minutes to President Club, 85M+ emails. | Writer asks for 2-4 lowercase words naming their topic. Gate caps subjects at 7 words. |
| "Quick question" and "partnership" style subjects underperform. Empty subjects get fewer replies. | Reply.io ("quick question" 2.5% replies vs 3% average); Belkins ("partnership opportunity" under 19% opens). | Gate rejects known weak subjects. |
| Personalized subject lines and real personalization roughly double replies. | Belkins (7% vs 3%); multiple practitioner reports of 2-3x. | Formats that use a real research fact or trigger are preferred by the picker; every claim must trace to a research fact. |
| Too many questions cut replies; three questions got about 30% fewer. | Reply.io. | One ask per email. Gate fails more than two question marks. |
| Links, images and attachments lower replies and hurt deliverability in a first email. | Reply.io; standard deliverability guidance. | No links in a first email (gate). Plain text only. |
| Interest-based asks beat meeting asks in a first email. | Gong and practitioner consensus; calendar links read as presumptuous. | Every first-touch format ends on an interest check, a yes/no question or an offer to share. |
| Most replies arrive after the first email; about 58% come from step one and the rest from follow-ups. | Instantly 2026 benchmark report; Prospeo (about 60% of replies after the first follow-up). | Follow-up and final formats in the library. Follow-ups see the earlier emails and must take a new angle. |
| Specific beats vague; losses move people more than gains. | Josh Braun's writing principles; loss aversion research. | PAS, Missed-revenue math and Poke the bear formats. Writer rule 8. Judge scores specificity. |
| Readers delete emails that sound templated or AI-written. | Practitioner consensus, 2024-2026. | Gate fails a list of AI tells ("I hope you're doing well", "leverage", "unlock", em dashes) and emails that open with "I" or "we". |
| Write about them, not you. | Lavender and most coaches; "you" focus is the most common edit. | Gate fails an email with far more I/we words than you words. |
| Mobile first: most cold email is read on a phone. | Twilio subject truncation data (iPhone shows 33-41 characters). | Paragraphs over 50 words fail; one or two sentences per paragraph. |

Sources:
[Reply.io data-backed insights](https://reply.io/data-backed-insights-on-effective-cold-emails/),
[Prospeo, how long should a cold email be](https://prospeo.io/s/how-long-should-a-cold-email-be),
[Prospeo, cold email frameworks](https://prospeo.io/s/cold-email-frameworks),
[Hunter, cold email word count](https://hunter.io/blog/cold-email-word-count),
[Josh Braun writing principles (summary)](https://skills.cat/skills/sachacoldiq/coldiq-s-gtm-skills/josh-braun-copywriting).

## The format library

26 formats in [`finley/formats.py`](../finley/formats.py), each with its beats, word range, ask,
when to use it, what research it needs and the principle it rests on. Run `finley formats list`.

- **First touch (20):** Quick question, Trigger, Timeline hook, New in seat, Observation + curiosity,
  Hypothesis, Poke the bear, Question-Value-CTA, Problem-Agitate-Solve, Before-After-Bridge,
  Relevance-Reward-Request, Peer proof, Missed-revenue math, Give first, Their own words, Switch
  story, No-oriented question, Owner-direct (local), Mutual connection, Right person?
- **Follow-up (4):** Bump with something new, New angle, Proof follow-up, Short bump.
- **Final (2):** Breakup, Who should I ask?

A format only unlocks when the data supports it: Trigger needs a real trigger, Before-After-Bridge
needs a case study you supplied, Mutual connection needs a `mutual_connection` column on the lead.
Research tags every fact with a kind (trigger, hiring, new_role, tool, published, service, location,
size, review, other), so Switch story only appears when research found a tool they use, Their own
words only when it found something they published, and New in seat only when the person is new.

## Best of two

Each Opus call writes two genuinely different versions of the email (different first line, lead
fact and subject). Both go through the checks, the judge scores the ones that pass, and the best one
is kept. Extra cost is a few hundred output tokens and one more Haiku call per email. Set
`FINLEY_WRITER_VARIANTS=1` to turn it off or 3 for more choice.

## How a format gets chosen

1. Finley lists the formats this lead's research can support, minus any already used on this lead.
2. Haiku ranks up to three that fit and names the angle to lead with.
3. Once formats have at least 20 sends between them, Finley picks among those three by Thompson
   sampling on positive reply rate. Formats that earn replies get used more, and newer ones still
   get tried. Turn it off with `FINLEY_LEARN_FORMATS=0`.
4. The choice and how it was made are saved with the email (`gate_json.pick`).

## Finding more winning formats

Put emails that got replies in a text file, separated by lines of `---`, then:

```
finley formats mine winners.txt --out my_formats.json
FINLEY_FORMATS_FILE=my_formats.json finley bench
```

Opus extracts the shape of each email (never its facts), skips shapes the library already has and
anything that relies on tricks, and writes format specs you can load. Keep a mined format only if
it holds up on Email Bench and in a live test.
