---
name: harness-scan
description: Read the next batch of not-yet-scanned days in <owner>'s thought log and turn them into harness notes, states, dependencies and todo pins. Use it for the regular daily scan or to carry on scanning older history.
---
# harness-scan

The rules in the harness's CLAUDE.md apply: read the thought log a day at a time, and leave
<owner>'s own lists alone. Every command below is bash (on Windows, Git Bash; see CLAUDE.md for
why not PowerShell 5.1). Run them from the tools repo with HARNESS_ROOT naming the harness:

```
cd "<path to tools>"
export HARNESS_ROOT="<path to harness>"
```

`<pass>` names a folder under `scans/`, for example `discovery-1`. Before its first scan it needs
a `pass.md` that says what the pass is looking for.

## Steps
1. Choose days: `python scripts/thought_log.py next --pass <pass> -n 20 --kb 100`.
   The list starts at the most recent day not yet read, so fresh days are handled first and
   older history is worked through afterwards. Lower `-n` and `--kb` for a short daily run.
2. Read each chosen day in full: `python scripts/thought_log.py show <id>`.
3. Look for an existing note before creating one. Read `$HARNESS_ROOT/concordance/INDEX.md`
   and search names and aliases: `grep -ril "<name>" "$HARNESS_ROOT/concordance"`. If a note
   exists, add to it; never create a second note for the same thing.
4. Write down what the day says, with the day (YYYY-MM-DD) in every Log line:
   - Something new worth following (project, person, machine, place, organisation, concept):
     a short note via `note.py new`.
   - Progress or a decision: one dated `note.py log` line on the right note.
   - Plans that hang on something else ("once X is done", "still waiting for", "stuck until",
     "must check with <person>"): set `depends_on`, `state: waiting` or `blocked`,
     `waiting_on`, `nudge_every`, and log it.
   - Something finished: `state: done` and `done_on: <day>`.
   - Something dropped: `state: dead`, `done_on: <day>`, and a Log line giving the reason.
   - The tracking marker (see CLAUDE.md): the thing must end up with a note that has a `state`.
   - Material older than <cutoff>: new notes get `state: proposed`, `dormant`, or no state.
   - Anything uncertain: `note.py question`, not a guess written down as fact.
5. Todos (skip unless the todo add-on is set up):
   `python scripts/todo_items.py list --unpinned --source open` and
   `python scripts/todo_items.py list --unpinned --source inline` give the ids. When a day
   explains an item, tie it to its note:
   `python scripts/todo_items.py pin <id> --context <day> --notes "[[slug]]" --why "<one line>"`
   If the day shows the item already happened, add `--status done-elsewhere`; if it was given
   up, `--status dead`. Dates in `--why` are always YYYY-MM-DD.
   Next, `python scripts/todo_items.py orphans` lists pins whose item no longer has the same
   first line (it was ticked, reworded or deleted). Pin the item again under its new id when you
   can find it; otherwise close the old pin with
   `python scripts/todo_items.py pin <old id> --status done-elsewhere`.
6. Record days as read only once the notes for them are saved:
   `python scripts/thought_log.py mark --pass <pass> <id> [<id> ...]`
7. Check links: `python scripts/links.py`, and fix any broken `[[link]]` you added.
8. Rebuild the index with `python scripts/links.py index`, then commit the harness folders
   that exist (not `git add -A` on the whole tree; other work may be in progress there):

   ```
   for d in concordance pins scans reviews; do if [ -e "$HARNESS_ROOT/$d" ]; then git -C "$HARNESS_ROOT" add -A -- "$d"; fi; done
   git -C "$HARNESS_ROOT" commit -m "scan: <first>..<last>"
   ```
   If the harness has a private remote, push. If pushing fails, write that in the scan log and
   carry on.
9. Add a few lines to `$HARNESS_ROOT/scans/<pass>/log.md`: which days, which notes were created
   or changed, which todos were pinned, and the questions for <owner>.
10. Finish the report to <owner> with `python scripts/todo_items.py stale` (three items unless
    you pass `-n`): items still open in <owner>'s lists that the harness believes are finished.
    <owner> ticks them; you never change the list. For each one:
    - Copy its first line character for character from the list, so <owner> can search for it,
      and confirm it with `grep -F "<line>"` on the list file.
    - Below it, suggest the exact line <owner> could add under the item, in the format they use
      for progress notes, saying why it is finished and which day(s) show it. Read the evidence
      first (the note's Log, `thought_log.py show`); when it is not conclusive, tell <owner>
      so rather than suggesting a line.
    - If <owner> says the item is still open, pin it to a note that is still live, and it will
      leave the report.

## Tips
- `python scripts/thought_log.py grep REGEX` ignores case unless you add `-c`. Add `-a` to see
  all matching lines of a day and `-w 400` for longer lines.
- To undo a pin made by mistake: `python scripts/todo_items.py unpin <id>`. The pins file is
  only ever changed through `todo_items.py`.
- `python scripts/priority.py explain <slug>` shows the parts of a note's score.
