# Publishing Nuke Code Editor 2.0 - step by step

## Part 1 - GitHub

### 1. Create the repository
1. Go to https://github.com/new
2. Repository name: `NukeCodeEditor`
3. Description: `PyCharm-style Python IDE for Foundry Nuke (PySide2 / PySide6)`
4. Public. Leave "Add README / .gitignore / license" **unchecked** (they already exist in this folder).
5. Click **Create repository**.

### 2. Push the code
Open a terminal inside the `NukeCodeEditor` folder:

```bash
git init
git add .
git commit -m "Nuke Code Editor 2.0.0"
git branch -M main
git remote add origin https://github.com/<YOUR_USERNAME>/NukeCodeEditor.git
git push -u origin main
```

### 3. Tag and release
```bash
git tag v2.0.0
git push origin v2.0.0
```
Then on GitHub: **Releases -> Draft a new release**
- Tag: `v2.0.0`  - Title: `Nuke Code Editor 2.0.0`
- Description: paste the 2.0.0 section from `CHANGELOG.md`
- Attach `NukeCodeEditor_v2.0.0.zip` (drag into "Attach binaries")
- **Publish release**

### 4. Polish
- Repo **About** -> Topics: `nuke`, `foundry`, `vfx`, `compositing`, `python`, `pyside2`, `pyside6`, `ide`, `script-editor`, `pipeline`
- Add `docs/screenshot.png`, then uncomment the image line in `README.md`
- Optional: Issues enabled for bug reports

## Part 2 - Nukepedia

Field names may be labelled slightly differently on the site; the content below maps to them.

### 1. Account
Register / log in at https://www.nukepedia.com

### 2. Add the tool
Open the upload page (**Tools -> Add Tool**, or **Upload** in the menu) and fill in:

| Field | Value |
|---|---|
| Title | Nuke Code Editor |
| Version | 2.0.0 |
| Category | Python (scripting / script editor tools - pick the closest) |
| Nuke versions | 13, 14, 15, 16+ |
| Platforms | Windows, Linux, macOS |
| Author | Nitin Kashyap |
| Website / Source | your GitHub repo URL |
| File | `NukeCodeEditor_v2.0.0.zip` |
| Thumbnail / images | screenshot(s) of the editor, plus `NukeCodeEditor.png` as icon |
| License | Freeware |

### 3. Short description (paste)
> A PyCharm-style Python IDE for Nuke. Syntax highlighting, inspections, completion, a real debugger with breakpoints, run inside Nuke / external Python / nuke -t, Python console, terminal, project tree, Search Everywhere, refactoring, and a TD Tools dock (node browser, knob inspector, snippets, scene utilities). PySide2 and PySide6, single file, no dependencies.

### 4. Full description (paste)
Use the **Highlights** and **Quick start** sections of `README.md`.

### 5. Installation text (paste)
> 1. Extract the zip into your `~/.nuke` folder (Windows: `C:\Users\<you>\.nuke`).
> 2. Result: `init.py`, `menu.py` and `td_tools/nuke_code_editor.py` in `~/.nuke`. If you already have `init.py` / `menu.py`, paste the supplied contents at the end of yours instead of overwriting.
> 3. Restart Nuke.
> 4. Open via **NK_Nuke Code Editor -> Nuke Code Editor** or press **Alt+E**.

### 6. Submit
Preview, submit, and wait for approval. Upload updates later via **My uploads -> Edit -> new version** with an updated zip and changelog.

## Release checklist
- [ ] Tested in Nuke 13/14/15 (PySide2) and 16 (PySide6) if available
- [ ] Clean install tested from the zip into a fresh `~/.nuke`
- [ ] Screenshot added
- [ ] Version matches in `menu.py` (`v2.0.0`), `CHANGELOG.md`, GitHub tag, Nukepedia form
- [ ] `session.json` / `settings.json` NOT included (they contain personal paths)
