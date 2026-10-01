# Nuke Code Editor 2.0

**A PyCharm-style Python IDE for Foundry Nuke — PySide2 and PySide6, one file, no dependencies.**

Author: **Nitin Kashyap** · Version: **2.0.0** · Build: 1 Oct 2026

<!-- Add a screenshot: save it as docs/screenshot.png and uncomment the next line -->
<!-- ![Nuke Code Editor](docs/screenshot.png) -->

---

## Highlights

- Real code editor: syntax highlighting (Darcula / Light), folding, bracket matching, auto-pairs, smart Enter / Backspace / Home
- **Inspections**: syntax errors, unresolved references, unused imports and variables, bare `except`, mutable defaults, long lines
- **Completion + docs** against live Nuke objects (`nuke.selectedNode().`), optional `jedi`
- **Real debugger** (built on `bdb`): gutter breakpoints, step over / into / out, call stack, variables, evaluate expression, runs inside Nuke
- **Run** inside Nuke (one undo step per run), in external Python, or in `nuke -t`
- Python Console (REPL sharing Nuke's namespace), Terminal, Problems, Find, TODO panels
- **TD Tools dock**: node browser, knob inspector, snippets, scene utilities, "selection → Python rebuild"
- Navigation and refactoring: Search Everywhere (double Shift), go to declaration (incl. Nuke's own Python source), find usages, rename, surround with, reformat (black / autopep8 / built-in), optimize imports
- Session restore: tabs, unsaved text, breakpoints, bookmarks, layout and settings

## Requirements

| Item | Details |
|---|---|
| Nuke | 13, 14, 15 (PySide2, Python 3.7–3.10) and 16+ (PySide6) |
| Python packages | None required |
| Optional | `jedi` (smarter completion), `black` or `autopep8` (reformatting). Auto-detected, with built-in fallbacks |
| OS | Windows, Linux, macOS (developed and tested on Windows) |

## Installation

Your Nuke user folder is `~/.nuke` (Windows: `C:\Users\<you>\.nuke`). Copy the package so it looks like this:

```
~/.nuke/
├── init.py
├── menu.py
└── td_tools/
    ├── nuke_code_editor.py
    └── NukeCodeEditor.png
```

**Already have an `init.py` / `menu.py`?** Don't overwrite them. Open the supplied files and paste everything below the docstring at the end of yours.

Restart Nuke. A new menu **NK_Nuke Code Editor** appears in the menu bar, and the Script Editor prints a startup line:

```
NK_Nuke Code Editor v2.0.0, build 1 Oct 2026.
```

### Alternative (no `init.py`)

Put `nuke_code_editor.py` in any folder on `NUKE_PATH` or `PYTHONPATH`, then keep only `menu.py`.

## Usage

Open the editor with **NK_Nuke Code Editor → Nuke Code Editor** or press **Alt+E**.
Docked version: **NK_Nuke Code Editor → Nuke Code Editor (panel)** or **Windows → Custom → Nuke Code Editor**.

> Tip: use the floating window for daily work. When docked, Nuke's own shortcuts (Ctrl+S, Ctrl+N, Ctrl+O, Ctrl+W …) can clash with the editor's.

### Quick start

| Goal | How |
|---|---|
| Run current tab inside Nuke | `Ctrl+Return` / `F5` / `Shift+F10` |
| Run selection / current line | `Ctrl+Shift+Return` |
| Run externally or in `nuke -t` | Toolbar combo box (Inside Nuke / Python (external) / Nuke -t). Set interpreter in File → Settings |
| Debug | Click the gutter for red breakpoints, then `Shift+F9` |
| Open anything | Tap `Shift` twice (Search Everywhere) |
| Use a project folder | File → Open Folder as Project (`Ctrl+Shift+O`) |
| Insert node code | TD Tools dock → Nodes → *Sel → Python rebuild* |

### Live templates

Type an abbreviation, then `Tab`:
`main def defs class for fori if ife try with prn prop log` plus Nuke-specific
`nsel nall nread nwrite ntn ncn nundo ncb nkc nprog`
(e.g. `nsel` = loop over selected nodes, `nundo` = undo-group boilerplate).

### Debugger

| Action | Key |
|---|---|
| Toggle breakpoint | Gutter click / `Ctrl+F8` |
| Start debugging | `Shift+F9` |
| Resume / Step over / Step into / Step out | `F9` / `F8` / `F7` / `Shift+F8` |
| Stop | `Ctrl+F2` |

While paused, Nuke's UI is blocked — press Resume or Stop before working in Nuke. Don't debug during renders.

### TD Tools dock

| Tab | Purpose |
|---|---|
| Nodes | Filterable node list (errors in red). Click selects, double-click zooms. Insert `toNode()` calls, name lists, or full Python rebuilding the selection with connections |
| Inspector | Auto-refreshing knob table, optional "only changed" filter, double-click inserts the accessor |
| Snippets | 15 built-in TD snippets plus your own (Insert, Run, Save as…, Delete) |
| Utils | Script info, class counts, missing Read files, error nodes, unused nodes, Write paths, select by class, toggle disable, reload all Reads |

### Keyboard shortcuts (most used)

| Run / Debug | Navigate |
|---|---|
| Run `Ctrl+Return` | Search Everywhere `Shift Shift` |
| Run selection `Ctrl+Shift+Return` | Go to File `Ctrl+Shift+N` |
| Debug `Shift+F9` | Find Action `Ctrl+Shift+A` |
| Stop `Ctrl+F2` | Recent files `Ctrl+E` |
| Clear output `Ctrl+L` | Declaration / Usages `Ctrl+B` / `Alt+F7` |

| Edit | Code |
|---|---|
| Comment `Ctrl+/` | Complete `Ctrl+Space` |
| Duplicate / Delete line `Ctrl+D` / `Ctrl+Y` | Quick docs `F1` |
| Move line `Alt+Shift+Up/Down` | Rename `Shift+F6` |
| Find / Replace `Ctrl+F` / `Ctrl+R` | Surround With `Ctrl+Alt+T` |
| Find in files `Ctrl+Shift+F` | Reformat `Ctrl+Alt+L` |
| Settings `Ctrl+Alt+S` | Optimize imports `Ctrl+Alt+O` |

Full list inside the editor: **Help → Keyboard Shortcuts** (`Ctrl+Shift+F1`).

## Settings and data files

All persistent data lives in `~/.nuke/NukeCodeEditor/`:

| File | Content |
|---|---|
| `settings.json` | Preferences, recent files/projects, window geometry |
| `session.json` | Open tabs, unsaved text, breakpoints, bookmarks |
| `snippets.json` | Your saved snippets |
| `tmp/` | Temporary copies of unsaved tabs for external runs |

To reset the editor: close it and delete the file(s) you want to reset.

## Troubleshooting

| Problem | Fix |
|---|---|
| No menu appears | Check `menu.py` is in `~/.nuke`; look for `[NukeCodeEditor]` lines in the Script Editor |
| `ImportError: nuke_code_editor` | `init.py` not loaded or `td_tools/` missing. Add manually: `nuke.pluginAddPath('/full/path/to/td_tools')` |
| Shortcut does nothing when docked | Use the floating window, or change the clashing Nuke shortcut |
| "No interpreter found" (external run) | File → Settings → Python interpreter, e.g. `python3` or a full path |
| `nuke -t` run fails | Needs a Nuke licence and a Nuke executable on `PATH` |
| Wrong "unresolved reference" warnings | Dynamic names (`exec`, `globals()`) can't be seen. Files with `from x import *` or `exec()` skip the check |
| Odd editor state | Delete `settings.json` and `session.json` |
| Slow on huge files | Inspections turn off above roughly 300–500 KB of text |

## Known limitations

- Rename, Reformat and Optimize Imports work on the current file only (no project-wide refactoring)
- Rename is token based: every identifier with that name in the file changes (strings and comments are left alone)
- Folding is indentation based; no git / local-history UI, no multi-caret editing, no split view
- Debugging pauses Nuke's UI

Please report issues together with your Nuke version and the Script Editor output.

## Uninstall

Delete `td_tools/nuke_code_editor.py` (and the icon), remove the supplied blocks from `init.py` and `menu.py`, and optionally delete `~/.nuke/NukeCodeEditor`.

## License

Copyright (C) 2026 Nitin Kashyap. All rights reserved. Free to use; see [LICENSE](LICENSE).
