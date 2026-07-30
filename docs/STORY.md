# The Story of Leviathan

This is the human version of the project. For the technical changelog —
every fix, every root cause, every commit — see [`PROGRESS.md`](PROGRESS.md).
This document is for anyone who wants to understand *what happened* without
needing to read code.

---

## The idea

Every day, thousands of contracts trade on [Kalshi](https://kalshi.com) —
a regulated exchange where you can bet on whether something will actually
happen. Will a cabinet official resign. Will a bill pass the Senate. Will
a hurricane make landfall. Each contract has a price, and that price is,
in effect, the crowd's best guess at a probability.

The question behind this whole project is simple to ask and hard to
answer honestly: **can an AI reading the news actually beat that crowd?**

Not "can it sound confident." Beat it — produce probability estimates
that are measurably more accurate than just looking at the price. That's
a much higher bar than it sounds, because prediction markets are
*designed* to be hard to beat. The price already encodes everything
every trader in that market knew and believed. Adding value on top of
that means finding real, current information the market hasn't priced in
yet — not just restating the price back with more words.

So Leviathan was built to actually test that, honestly, with real money
math (even though no real money is ever risked) and a real paper trail of
every guess it made and whether it was right.

## The discipline: measure first, believe nothing yet

The project's guiding rule, stated plainly and followed even when it's
uncomfortable: **build the measuring instrument before trusting any
result it produces.** Concretely, that means:

- Every estimate the system makes gets logged *before* anyone knows the
  outcome, alongside the market's own price at that moment — so months
  later, both can be graded against reality on equal footing.
- A running scoreboard exists specifically to catch the system fooling
  itself — comparing "how good was our guess" against "how good would
  just believing the market price have been." If the market price wins,
  that's reported as a finding, not buried.
- Before any new idea (like sizing bets by confidence, or auto-adjusting
  the model's calibration over time) gets turned on, there's a
  pre-agreed sample size it has to earn first. Not because the idea is
  bad, but because with only a handful of results so far, almost any
  pattern you look for will "work" by pure chance — and it's too easy to
  quietly keep the ones that do and forget the ones that don't.

This shows up constantly in the actual story below: things get built,
tested, and then deliberately left switched off until there's enough real
data to trust them.

## Act I — Finding out the crowd is hard to beat (and admitting it)

Early on, a metric was added that simply asks: *if you'd just copied the
market's own price instead of using the AI's estimate, how would that
have scored?* This is an uncomfortable thing to measure on purpose,
because it's entirely possible the answer is "better than our system."

It was. On the small sample collected so far, just believing the market
price outscored the AI's estimate by a wide margin — one single
overconfident wrong call (a cabinet-departure prediction that went against
the market and lost) was enough to flip the comparison. That's not a
flattering result, and it went in the public record anyway, with the
honest caveat that a handful of results isn't enough to conclude anything
permanent — only enough to know the system is now watching for it.

A written commitment followed almost immediately: a **pre-registered kill
criterion**, dated and locked in before any more data could bias the
decision — a specific statistical bar the system has to clear once enough
real results exist, agreed to in advance specifically so nobody could move
the goalposts later if the answer turned out to be no.

## Act II — Teaching the system to doubt itself

If the AI is shown the market's current price while it's making its guess,
how do you know it isn't just parroting that price back with a
justification attached? That's a real risk with any system that gets to
see the answer key while forming its opinion.

The fix: a second, "blind" version of the scorer that never sees the price
at all — no price line, and every instruction in its briefing that
implicitly leaned on price (there turned out to be more of these hidden
throughout than expected) rewritten to stand on its own. It runs
occasionally, quietly, in parallel, purely as a scientific control: does
the *price-aware* version actually know something the *blind* version
doesn't? Not yet turned on for real, deliberately, until the real cost of
running it is worth spending.

The same instinct — "don't let the system cheat, even by accident" —
also produced a companion project: a way to replay the system's scoring
process against markets that have *already* resolved, so its instrument
could be checked against known outcomes without waiting months for new
ones to happen naturally. Built carefully, with an explicit, permanent
asterisk stapled to it: any AI re-reading old news already has some idea
how the story ended, so this can sharpen the tool, but it can never be
cited as clean proof of live skill.

## Act III — The bugs that had been there the whole time

A full top-to-bottom audit of the codebase (six independent reviewers,
each covering a different slice) turned up eleven real, confirmed
defects. Two stand out:

- **The order-book pressure signal had never worked, from the day it was
  written.** It was reading a field name that didn't exist in Kalshi's
  real data. It didn't error — it just silently reported "no signal"
  forever, and nothing about the system's behavior gave any hint that
  anything was wrong.
- **The whale-detection alarm had the same problem, for the same
  reason** — it was calling an API endpoint that didn't actually exist,
  and had been quietly seeing zero trades, always, since it was built.

Both are the same lesson in two costumes: a system that fails by going
silent is far more dangerous than one that fails loudly, because nothing
*looks* broken. Both are now fixed, and both now have tests that
reproduce the exact original failure before confirming the fix — so if
either regresses, it'll be caught immediately instead of quietly again.

A smaller, later version of the same lesson: a bug meant that whenever the
AI flagged unusual large trades but ultimately passed on betting, that
whale-detection data was silently thrown away before it reached the
database — for the majority of the system's whole history, since most
flagged markets end in a pass. The fix was one line. The loss of data
already logged before the fix, though, was permanent — an honest gap
noted in the record rather than quietly smoothed over.

## Act IV — The whodunit that took three tries

Early on, the team wanted every signal to include a clickable link back to
the real Kalshi market. The obvious approach — guess the URL from the
ticker — was tested and rejected outright: Kalshi's market pages turned
out to return "success" for *any* URL, real or completely made up, with
nearly identical content either way. No number of clever guesses would
ever have told a real market from a fake one; that path was a dead end,
and the investigation said so instead of shipping a broken feature that
merely looked like it worked.

The user asked to look again anyway. That second pass found something the
first one missed: three independent pieces of evidence from Kalshi
itself — its own documentation written for AI agents, its own site map,
and one specific link on the site that behaves *differently* for a real
market versus a fake one (the only place that distinction actually showed
up). Combining all three confirmed a real, working link pattern — proof
that persistence past a "this is impossible" conclusion sometimes finds
the exception.

## Act V — The mystery of the wallets that never win

A parallel idea: instead of (or alongside) the AI's own judgment, track
what genuinely successful traders on a related betting platform are doing
right now, and treat their positioning as a signal.

The problem: after weeks of scanning, running the numbers on hundreds of
candidate traders, **not one has ever qualified.** Every single trader who
had enough of a track record to even evaluate turned out to have a
real-world win rate of exactly zero percent — not "poor," *zero*, across
as many as 161 settled bets for one wallet.

That number is strange enough that it deserved a real answer, not a shrug.
So the investigation went straight to the source: pulled that wallet's
actual betting history directly from the platform itself. The answer was
almost poetic. Every bet that had **already resolved** was a total loss —
long-shot wagers on things like a lasting US-Iran peace deal by a
now-passed deadline, bought cheap and in enormous size. But every
position still showing a *paper* profit was one that **hadn't resolved
yet** — some of them showing gains of over 200%, sitting open, unsettled,
still just a bet in progress.

In other words: that wallet's headline "$1.37 million a month" reputation
was built entirely on bets that haven't finished yet — lottery tickets
still in someone's pocket, not cash in hand. It's not a bug in the
tracking code. It's a real, if humbling, fact about how "biggest paper
gains" leaderboards can reward pure long-shot gambling as easily as they
reward genuine skill — and it explains, with hard evidence instead of a
guess, why this particular way of finding smart money hasn't found any yet.

## Act VI — Watching itself while no one's looking

A system that only runs when someone's there to check on it isn't
actually unattended. So the project built its own watchdog: something
that notices if the daily run silently stops firing, and something else
that refuses to process what looks like a broken response from Kalshi's
own API rather than quietly treating garbage as real data. Both write to
the same kind of paper trail as everything else — dated, timestamped,
explained.

That discipline paid off almost immediately in an unglamorous but telling
way: a scheduled run and a manually-triggered one landed two minutes apart
one morning, producing what looked like a duplicate. Rather than shrug it
off, the timing was traced back through the machine's own task history —
the scheduled run had been quietly skipped that day (the laptop happened
to be running on battery, which Windows treats as a reason not to start
a background job) and only caught up once conditions allowed, purely by
coincidence landing right next to a manual run. Nothing was actually
wrong. But proving that took real investigation, not a guess — and the
system's own diagnostic logging was upgraded on the spot so the next time
it happens, the answer is immediate instead of circumstantial.

## Where things stand

As of today, the system has logged a small number of real, graded
predictions — nowhere near enough to say anything definitive yet, and
the project is explicit about that rather than overselling early results.
What does exist: a disciplined measurement apparatus, a growing paper
trail of exactly what it got right and wrong and why, several honest
dead ends recorded instead of hidden, a small number of real defects found
and fixed rather than left to quietly corrode results, and a handful of
promising ideas built and ready — deliberately still switched off,
waiting for enough real evidence to justify turning them on.

That's the actual bet this project is making: that showing your work
honestly, including the parts where the crowd was smarter than you, is
worth more in the long run than a headline number nobody can trust.
