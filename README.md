# harness_tools

Tools for an LLM "chief of staff" that works for one person. The person and the model share a
folder of small markdown notes (the *harness*); a few Python scripts rank, check and summarise
it. This README is written for that person and for their agent. A thought log or a todo file is
optional: everything works without them.

## The idea

- **One note per thing.** Each project, person, machine, place, organisation or concept gets one
  markdown file under `concordance/<kind>/<slug>.md`. YAML front matter holds the facts the
  scripts need (state, base priority, dependencies); the body holds a short summary, a dated
  `## Log` and `## Open questions`. Notes refer to each other as `[[slug]]`, so the folder is a
  graph that the person can browse in any markdown editor and the model can read with plain
  file tools.
- **Read live, don't cache.** The scripts compute everything (ranking, last-touched dates, todo
  coverage, link health) from the notes and source files each time they run. A note records
  what happened and when; it does not store a score or a copy of something that lives elsewhere.
- **The model keeps the headers true, the person steers.** As things happen, the model moves
  states, adds Log lines, records dependencies and asks questions. The person sets base
  priorities, confirms proposed notes, turns knobs and answers questions.

## What it adds to an LLM

- **Memory across sessions.** A new session reads the notes instead of starting from nothing.
- **Ranking it can explain.** `priority.py` is deterministic. `priority.py explain SLUG` lists
  every term of a score, so the model can say *why* something is on top, and the person can
  disagree with a specific term.
- **Safe edits.** `note.py` changes one field or adds one bullet and keeps every other byte of
  the file, including comments and CRLF line endings. Writes are atomic. No ad-hoc rewrite
  scripts that reformat a note by accident.
- **Link checking.** `links.py` finds broken `[[links]]`, duplicate slugs, notes with no `type`,
  and tracked notes that can never age.
- **A daily digest.** `review.py` writes one page: top items, overdue nudges, just-unblocked
  work, what is waiting, what needs confirming, and the open questions for the person.
- **Steering without rewriting.** A *knob* pushes a note down (or lifts it) by an amount that can
  fade over time or end on a date or when another note is done. The person says "not until the
  beds are built"; the model adds a knob instead of editing the base priority and forgetting why.

## Note format

```
---
type: project            # the kind, written by note.py from the folder (see `kinds`)
state: waiting           # see "Note states"; leave out for reference notes (people, places...)
base: 2                  # how much it matters on its own (default 1)
aliases: [compost, the bins]
mentions: ['(?i)\bcompost\b']   # regexes; the latest thought-log day matching them = last touched
nudge_every: 7           # days
waiting_on: "[[sam]]"    # free text or a link; shown in the review
depends_on: ["[[lumber-order]]"]
part_of: "[[garden-season]]"
knobs:
  - amount: 3
    set: 2026-09-29
    until: "[[raised-beds]] done"
    why: beds first, then the supper
---
# Compost bins
Three-bay compost bins from old pallets, next to the shed.

## Log
- 2026-10-02: [[sam]] said he would collect pallets. (one line per event, dated, oldest first)

## Open questions
- Is anyone collecting kitchen scraps, or only garden waste?
```

Slugs are lowercase-hyphenated and unique across all kinds. File names starting with an
uppercase letter or `_` (such as the generated `INDEX.md`) are not notes.

## Note states

| state | meaning | ranked? | ages? |
|---|---|---|---|
| active | being worked on | yes | yes |
| waiting | waiting on someone; pair with `waiting_on` and `nudge_every` | yes | yes |
| parked | set aside on purpose | yes | slowly (`age_rate` 0.005 instead of 0.02) |
| blocked | cannot start; pair with `depends_on` | yes | yes |
| proposed | the model thinks it should be tracked; the person has not said yes | `--all` only | no |
| dormant | known, asleep, not to be worked on now | `--all` only | no |
| done | finished; set `done_on` so dependents get the unblock bump | no | - |
| dead | dropped; set `done_on` too (the day it was dropped) | no | - |
| (none) | a reference note: person, place, concept, org | no | - |

A note is also left out of the default list (it shows with `--all`), whatever its own state,
while its `depends_on` names a tracked note (one with a state) that is not done or dead; it
still pushes pressure onto that dependency. A dependency with no state, or with no note at all,
does not hide it (`priority.py` lists missing ones under "depends_on names with no note yet").
So a `state: blocked` note with no unfinished tracked dependency stays in the default list.
For `depends_on` and for knob conditions, `dead` counts as finished, like `done`.

Proposed and dormant notes do not age, so an old idea found in old material does not shoot up
the list. When the person confirms one, set its state and `last_touched` to the confirmation
date, so aging starts from that day. `review.py` lists proposed notes under
"Proposed - please confirm".

## The priority model

```
score = base + age_rate * days_untouched + nudge_overdue + unblock_bump - knobs
```

- **base**: `base`, default 1.
- **aging**: `age_rate` per day since the note was last touched; default 0.02, and 0.005 for
  `parked` (both in `DEFAULTS` / `STATE_AGE_RATE` in `scripts/priority.py`). A note's own
  `age_rate` wins. *Last touched* is the newest of `last_touched`, the latest thought-log day
  matching `mentions` (thought log add-on), and the newest dated `## Log` bullet (only when
  `aging.count_log_entries` is on, which is the default without a thought log). Log dates after
  today are ignored.
- **nudge_every**: once `days_untouched` exceeds it, adds `(days - nudge_every) / nudge_every`.
- **unblock bump**: when every `depends_on` note is done or dead, adds
  `UNBLOCK_BUMP * 0.5 ** (days since the newest done_on / UNBLOCK_HALF_LIFE)`, that is 3.0
  halving every 14 days. Dependencies without `done_on` are skipped when finding the newest
  date; if none has one there is no bump (the note still stops being blocked). So set
  `done_on` whenever a note becomes done or dead.
- **pressure**: a dependency is ranked at least `PRESSURE` (0.9) times the score of whatever
  waits on it, along whole chains, so a dull blocker rises when something important is stuck
  behind it.
- **knobs**: each knob subtracts `amount` (negative lifts). With `half_life` the amount decays as
  `amount * 0.5 ** (days since set / half_life)`. With `until` it stops applying on that date,
  or once `"[[slug]] done"` is done or dead. Without either it applies for good.

### Worked example (example/)

`example/` is an invented community garden. Run from the repo folder on 2026-11-11 (scores age
with today's date, so your numbers will differ):

```
$ HARNESS_ROOT=example python scripts/priority.py --all
   8.2  tool-shed-roof                     waits on roof-permit
        base 8; +0.2 untouched 8d (since 2026-11-03)
   7.3  roof-permit                        waiting
        base 0.5; +0.1 untouched 14d (since 2026-10-28); pressure from [[tool-shed-roof]] -> 7.3
   5.9  compost-bins                       waiting
        base 2; +0.6 untouched 30d (since 2026-10-12); +3.3 nudge overdue (every 7d)
   5.8  raised-beds                        active
        base 4; +0.1 untouched 5d (since 2026-11-06); +1.7 unblocked 2026-10-30
   3.0  garden-season                      active
        base 2; +0.0 untouched 2d (since 2026-11-09); +1.0 lifted (keep the umbrella in view all season)
   3.0  greenhouse                         dormant
        base 3
   2.0  pollinator-strip                   proposed
        base 2
   1.0  harvest-supper                     active
        base 4; +0.0 untouched 1d (since 2026-11-10); -3.0 suppressed (beds first, then the supper)
   0.5  rain-barrel                        parked
        base 1.5; +0.1 untouched 25d (since 2026-10-17); -1.1 suppressed (wait out the autumn rains before fitting the downpipe)
```

Reading it:

- **Pressure.** The shed roof (base 8) waits on a permit with base 0.5 and `age_rate: 0.005`. On
  its own the permit would score about 0.6; it inherits 0.9 x 8.2 and sits on top of the
  default list, because filling in a dull form is what unblocks the roof. The roof itself only
  shows with `--all` while the permit is open.
- **Nudge.** The compost bins wait on a person with `nudge_every: 7`. Last touched 30 days ago,
  so `(30 - 7) / 7` adds 3.3. The date comes from the thought log (`mentions`), not from the
  Log bullets.
- **Unblock bump.** The lumber order was done on 2026-10-30, 12 days earlier:
  `3 * 0.5 ** (12/14)` adds 1.7 to the raised beds.
- **Knobs.** The supper's knob (amount 3, until `"[[raised-beds]] done"`) keeps it down until the
  beds exist. The rain barrel's knob (amount 2, `half_life: 30`, set 25 days ago) has faded to
  1.1. The garden's knob (amount -1, until 2099-01-01) lifts it.
- **Parked.** The rain barrel, 25 days untouched, gained 0.1 (25 x 0.005).
- **Unconfirmed.** The greenhouse (dormant) and the pollinator strip (proposed) show only their
  base and only under `--all`.

`explain` gives one note's terms:

```
$ HARNESS_ROOT=example python scripts/priority.py explain roof-permit
roof-permit  7.3  [waiting]
   base 0.5
   +0.1 untouched 14d (since 2026-10-28)
   pressure from [[tool-shed-roof]] -> 7.3
```

What happens as the world moves on (on a copy, same day): marking the permit done
(`note.py set roof-permit state done`, `note.py set roof-permit done_on 2026-11-11`) gives the
roof `+3.0 unblocked 2026-11-11` and a score of 11.2; marking the raised beds done ends the
supper's knob and it rises from 1.0 to its base 4.0.

## Optional add-ons

Both are switched on in `harness.yaml` (see `harness.example.yaml` for every key) and both are
safe to leave out.

### Thought log

A single running text file, one dated section per day, newest at the bottom. `thought_log.path`
names it; `date_line` (a regex with three groups) and `date_order` (`mdy`, `ymd`, ...) say how a
day starts. With it:

- **Segments.** Each day is a segment with an id like `2026-11-06`; a repeated date gets
  `2026-11-06#2`. `show ID`, `grep REGEX` (case-insensitive unless `-c`) and `stats` read it
  live, so the model never has to load a large file whole.
- **Scan passes.** A pass is a folder `scans/<pass>/` with `pass.md` (its intent) and
  `ledger.jsonl` (the days it has read). `next --pass P` lists unscanned days, newest first,
  within `-n` days and `--kb` kilobytes; today is skipped unless `--include-today`. `mark --pass
  P ID ...` records days as done. A day whose text changed after it was marked shows up again
  as `changed`. Each pass keeps its own ledger, so a pass with a different purpose begins
  with nothing read.
- **Aging from mentions.** A note's `mentions` regexes are matched against each day; the newest
  match is a touch. With a thought log present, Log bullets stop counting by default
  (`aging.count_log_entries` overrides it).
- **Date checks.** `dates` flags repeated stamps, stamps out of order, stamps that are not real
  dates, and stamps on `suspect_weekdays` (days the person never writes).
- **Inline todos.** Text after `inline_todo_marker` is read as a todo list (see below).
- **A marker convention (optional).** The person can agree on a short tag (for example `[track]`)
  that means "track this". No script reads it; it is an instruction to the model in CLAUDE.md:
  any day containing it must produce or update a tracked note.

```
$ HARNESS_ROOT=example python scripts/thought_log.py next --pass discovery-1 -n 3
2026-11-10	line 51	67 bytes	new
2026-11-09	line 48	80 bytes	new
2026-11-06#2	line 45	103 bytes	new
```

### Todo pins

A plain todo file, items separated by blank lines, `|` lines under an item being progress
notes. With `todo.split_marker`, items above the marker line are done and items below are open.

- **Stable ids.** An item's id is a short hash of its first line, so editing its progress lines
  keeps the id; changing the first line (for example ticking it) gives a new id.
- **Pins** tie an item to notes: `pin ID --notes "[[slug]]" --context DAY --why TEXT
  [--status S]`, with status `unknown`, `pinned`, `dead` or `done-elsewhere`. Pins are appended
  to `pins/todo.jsonl` (later lines win; `unpin` appends a reset).
- **orphans** lists pins whose item's first line is gone, so they can be re-pinned or closed.
- **stale** lists open items the harness thinks are finished: the pin says `done-elsewhere` or
  `dead`, or every pinned note is done or dead. The person ticks them; the harness never edits
  the list. Once ticked (moved above the marker), an item drops off.

```
$ HARNESS_ROOT=example python scripts/todo_items.py stale
1 with the pin saying finished/dead, 1 whose note is done (showing 2)

95a85496  thinking.txt inline todo #0  [pin says finished]
  ask the association about a second water tap
  pin: done-elsewhere (2026-11-11); Dana asked at the 11/09 committee meeting
  evidence days: 2026-11-09

7f530439  todo.txt #0  [its note is done; check]
  Lumber: check every board for warping before building
  note done: [[lumber-order]] done 2026-10-30
  evidence days: 2026-10-30
```

### Without them

Everything else works. A note's last touch comes from `last_touched` and its newest dated
`## Log` bullet, so logging events with `note.py log` is what keeps aging honest. In the example
with both add-ons removed from `harness.yaml`, the compost bins age from their last Log line
(2026-10-02, 40 days) instead of the thought-log mention (2026-10-12). `thought_log.py` says no
log is configured and exits; `todo_items.py` lists nothing; the review leaves its todo section
out. `links.py` reports tracked notes with nothing to age from as "ageless".

## Quick start

Requirements: Python 3 and PyYAML.

```
python -m pip install -r requirements.txt
```

1. **Make a harness folder** anywhere outside this repo, with a `concordance/` folder in it.
   A `harness.yaml` is optional; copy the parts of `harness.example.yaml` you need (at least
   `owner:` is worth setting).
2. **Point the scripts at it.** Keep the scripts in this repo. Either:
   - set `HARNESS_ROOT` and run `python scripts/...` from this repo folder (steps 3 and 4 below
     are written this way):

     ```
     export HARNESS_ROOT=~/harness              # bash
     $env:HARNESS_ROOT = "$HOME\harness"        # PowerShell
     ```
   - or give the harness a `harness.yaml` (an empty file is enough), `cd` into the harness (or
     any folder below it) and run the scripts by their full path, for example
     `python <path to tools>/scripts/priority.py`. The scripts look for `harness.yaml` in the
     current folder and its parents.

   With neither, they look next to their own `scripts/` folder, which is this repo.
3. **Write a first note** (run from this repo folder, with `HARNESS_ROOT` set):

   ```
   python scripts/note.py new projects fix-fence "Fix the fence" "Back fence is leaning." --state active
   python scripts/note.py log fix-fence "2026-11-01: two posts are rotten at the base."
   python scripts/note.py set fix-fence base 3
   ```
4. **Rank, check, review:**

   ```
   python scripts/priority.py
   python scripts/links.py
   python scripts/review.py            # writes reviews/YYYY-MM-DD.md; --stdout prints it instead
   ```

**Try the example** (read-only commands are safe on `example/`; copy it first for anything that
writes, such as `note.py`, `mark`, `pin` or `review.py` without `--stdout`):

```
HARNESS_ROOT=example python scripts/priority.py --all
```

PowerShell (`$env:` settings last for the whole session, so put the old value back, or a
later `note.py` would write into `example/`):

```
$old = $env:HARNESS_ROOT; $env:HARNESS_ROOT = "example"; python scripts/priority.py --all; $env:HARNESS_ROOT = $old
```

**Smoke test** (copies `example/` to a temp folder and runs every command against the copy):

```
python tests/smoke_test.py
```

## Working with an LLM

Copy `templates/CLAUDE.md` into the harness folder and fill in its placeholders (`<owner>`,
`<path to harness>`, `<path to tools>`, `<cutoff>`: how old material must be before new notes
from it start as proposed, and `<marker>`: the "track this" tag, if any). If the person keeps
a thought log, also copy `templates/skills/harness-scan/` to `.claude/skills/harness-scan/` in
the harness. Other agents can read the same files as plain instructions.

The commands in the templates are bash (Git Bash on Windows). Windows PowerShell 5.1 drops the
inner double quotes from arguments it passes to python, so `note.py set` values such as
`'["[[a]]", "[[b]]"]'` would be stored wrongly; CLAUDE.md shows the escaped form for the rare
case where PowerShell is the only option.

A daily routine:

1. **Scan.** The scan skill reads the next unscanned days of the thought log, updates notes with
   `note.py`, pins todos, checks links, regenerates `INDEX.md` and commits. Without a thought log,
   do the same from whatever the person tells the agent or hands it (meeting notes, messages).
2. **Update notes.** States, Log lines, `depends_on`, `waiting_on`, `nudge_every`, `done_on`.
3. **Review.** `python scripts/review.py` and read the digest with the person.
4. **Answer the questions.** The digest ends with "Questions for <owner>", gathered from every
   note's `## Open questions`. Once answered, the model records the answer in the note's Log
   (`note.py log`) and deletes the question (a prose edit).

Ground rules worth keeping (all in the template):

- **Never invent facts.** Label guesses "(guess)" and put them under Open questions.
- **Cite the source day** (or todo id) for every fact in a Log line.
- **Edit notes only with `note.py`**; plain edits only for prose (title, summary, rewording).
- **Never edit the person's own lists** (thought log, todo file). Propose the change instead.
- **Commit the harness to git** after each unit of work, staging only the harness folders
  (`concordance`, `pins`, `scans`, `reviews`) with `git -C <path to harness>`, so every change
  shows in a diff. The template has the exact commands.

## Safety

A harness holds private data: names, plans, health, money, whatever the person writes. Keep it
in its own private git repository, never inside this tools repo, and never push it anywhere
public. This repo's `.gitignore` ignores `harness.yaml`, `concordance/`, `scans/`, `pins/` and
`reviews/` at the repo root, as a guard in case someone points a harness at the repo by
mistake. Never copy credentials or other secrets into notes.

## Files

- `scripts/config.py`: finds the harness root and reads and checks `harness.yaml`.
- `scripts/note.py`: byte-preserving note edits: `new`, `log`, `question`, `set`, `unset`, `show`.
- `scripts/priority.py`: ranks notes with reasons; `explain SLUG`.
- `scripts/links.py`: `check`, `backlinks SLUG`, `orphans`, and `index` (writes `concordance/INDEX.md`).
- `scripts/review.py`: writes the daily review digest.
- `scripts/thought_log.py`: thought log add-on: `stats`, `show`, `grep`, `next`, `mark`, `dates`.
- `scripts/todo_items.py`: todo add-on: `list`, `show`, `pin`, `unpin`, `orphans`, `stale`.
- `harness.example.yaml`: every setting, commented.
- `example/`: an invented community-garden harness that uses every feature.
- `templates/`: `CLAUDE.md` and the `harness-scan` skill for an agent working in a harness.
- `tests/smoke_test.py` (with `smoke_lib.py`, `smoke_edges.py`): runs every command on a copy of `example/`.
- `requirements.txt`: PyYAML.

## License
MIT, copyright LaserLinc, Inc. See `LICENSE`.
