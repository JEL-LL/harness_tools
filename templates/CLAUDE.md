# Harness: how an agent works in this folder

This folder belongs to <owner>. It holds one small markdown note for each project, person,
machine, place, organisation or concept they care about, linked to each other with `[[slug]]`,
and a ranking script that brings neglected work back to the surface. Your job is to keep the
notes accurate as events happen. <owner> decides base priorities, approves your proposals and
answers your questions.

## Running the tools
The scripts live in a separate tools repo at <path to tools>. Every command in this file and in
the scan skill is written for bash (on Windows, Git Bash), run from the tools repo with
HARNESS_ROOT naming this folder:

```
cd "<path to tools>"
export HARNESS_ROOT="<path to harness>"
python scripts/priority.py
```

Why bash: Windows PowerShell 5.1 strips the inner double quotes from arguments it passes to
python, so a value such as `'["[[a]]", "[[b]]"]'` arrives as `[[[a]], [[b]]]`. `note.py` refuses
that for `depends_on` and `part_of`, but other keys are written as they arrive. If you must use
PowerShell, escape each inner quote as `\"`, for example `'[\"[[a]]\", \"[[b]]\"]'`, and check
the result with `note.py show`.

## Ground rules
- Work only from <owner>'s files and from what <owner> tells you. Do not make facts up. When you
  have to guess, write "(guess)" next to it and file it under `## Open questions`.
- Give a source for every fact: the thought-log day (YYYY-MM-DD), the todo id, or
  "told by <owner> on YYYY-MM-DD".
- Change notes through `scripts/note.py`. Hand edits are only for prose: the title, the summary
  line, or the wording of a bullet that already exists.
- The thought log and the todo file are <owner>'s own. Do not change them unless <owner> asks for
  that exact change; otherwise suggest it, quoting the line as it stands.
- If a thought log is configured, never open the whole file. Read it a day at a time with
  `scripts/thought_log.py` (`next` to choose days, `show` to read one, `grep` to search).
- Credentials, keys and other secrets never go into a note.
- Do not assume anyone's pronouns: use their name again, or they/them.
- After each finished piece of work, commit it in the harness repo, staging only the harness
  folders that exist (never the whole tree):

  ```
  for d in concordance pins scans reviews; do if [ -e "$HARNESS_ROOT/$d" ]; then git -C "$HARNESS_ROOT" add -A -- "$d"; fi; done
  git -C "$HARNESS_ROOT" commit -m "<what changed>"
  ```
- Python 3 and PyYAML: `python -m pip install -r requirements.txt` in the tools repo.

## Layout
- `harness.yaml`: settings such as the owner's name, the note kinds, and the optional thought log
  and todo file. All keys are optional; `<path to tools>/harness.example.yaml` documents them.
- `concordance/<kind>/<slug>.md`: the notes. The kind folders come from `kinds` in harness.yaml
  (default: projects, hardware, machines, people, concepts, places, orgs). A slug is lowercase
  with hyphens and must be unique across every folder, so `[[slug]]` works without a folder
  name. `concordance/INDEX.md` is rebuilt by `links.py index`; do not edit it.
- `scans/<pass>/`: `pass.md` says what a scan pass looks for, `ledger.jsonl` records the days it
  has read, `log.md` gets a few lines per scan session.
- `pins/todo.jsonl`: links between todo items and notes, written only by `scripts/todo_items.py`.
- `reviews/YYYY-MM-DD.md`: digests from `scripts/review.py`.
- When a fact already lives in a file a script can read, let the script read it rather than
  copying it into a note.

## Note format
```
---
type: project            # the note's kind (note.py fills it in from the folder)
state: active            # tracked notes only; see the list of states below
aliases: [Fence, the back fence]
mentions: ['(?i)\bfence\b']   # regexes; the newest thought-log day that matches counts as a touch
base: 3                  # optional; every ranking field is described in the priority.py docstring
depends_on: ["[[other]]"]
part_of: "[[parent]]"
---
# Title
A sentence or two saying what this is.

## Log
- 2026-11-01: an event or decision, dated by its source.

## Open questions
- something <owner> needs to confirm
```

States: `active` (in progress), `waiting` (on someone; also set `waiting_on` and `nudge_every`),
`parked` (put aside deliberately; ages slowly), `blocked` (set `depends_on`), `proposed` (you
suggest tracking it; <owner> has not agreed yet), `dormant` (known but asleep), `done`, `dead`
(dropped). For `done` and `dead`, also set `done_on`. People, places and concepts usually have
no state.

## Editing notes
`note.py` locates a note by its slug, writes only what it was asked to change (line endings and
every other byte stay as they were), files Log bullets by date and ignores an exact repeat:
- `python scripts/note.py new KIND SLUG "Title" "One-line summary" [--type T] [--state S] [--mentions REGEX ...] [--aliases A ...] [--part-of SLUG]`
- `python scripts/note.py log SLUG "YYYY-MM-DD: text"`
- `python scripts/note.py question SLUG "text"`
- `python scripts/note.py set SLUG KEY VALUE` (VALUE in YAML, e.g. `14`, `waiting`, `2026-11-01`, `'["[[a]]", "[[b]]"]'`)
- `python scripts/note.py unset SLUG KEY`
- `python scripts/note.py show SLUG`

## Ranking
- `python scripts/priority.py` lists tracked notes by score, each with its reasons. `--all` adds
  proposed and dormant notes and notes waiting on an unfinished dependency. `explain SLUG`
  breaks one score into its parts.
- When <owner> says "not now" or "only after X", add a knob instead of lowering `base`:
  `python scripts/note.py set SLUG knobs '[{amount: 3, set: 2026-11-01, until: "[[x]] done", why: "after x"}]'`
  Add `half_life: DAYS` to make it fade, or use `until: YYYY-MM-DD` to end it on a date.
  `set` replaces the whole `knobs` list, so repeat any knob that is already there.
- When <owner> approves a proposed or dormant note, change its state and set
  `last_touched` to the day of approval, so aging counts from then and not from old material.
- When something is finished or dropped, set `state: done` (or `dead`) and `done_on: <day>`.
  Once all of a note's dependencies are finished it gets a bump of 3.0 that halves every
  14 days, dated from the newest `done_on`; with no `done_on` there is no bump.

## Review
- `python scripts/review.py` writes `reviews/YYYY-MM-DD.md`; `--stdout` prints it instead.
- Walk <owner> through it: the top items, overdue nudges (who to chase), anything just
  unblocked, what is waiting, the proposed notes, and "Questions for <owner>".
- For each answer, add a Log line dated the day <owner> answered, then delete the question.

## Scanning
The scan skill is in `.claude/skills/harness-scan/SKILL.md` (only useful with a thought log).
- If a note comes only from material older than <cutoff, e.g. one year>, start it as
  `proposed` or `dormant` rather than `active`, and let <owner> wake it up.
- When a day contains <marker, e.g. [track]>, <owner> wants that thing followed: make sure it
  has a note, and that the note has a state.

## Quality bar
- A handful of well-sourced notes beats a pile of thin ones. Skip trivia and links seen once.
- When recording a decision, quote <owner> briefly rather than paraphrasing.
- After editing notes, run `python scripts/links.py` and repair any broken link you caused.
