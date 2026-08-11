# Automatic updater

The desktop GUI now checks for project updates before either the PySide6 or legacy Tk interface is imported.

## Normal behavior

Launching:

```powershell
python -m deltarune_agent gui
```

or double-clicking `Start AI GUI.bat` automatically checks the current Git branch against the matching branch on `origin`.

The updater only applies a **clean fast-forward**. It never uses `git reset --hard`, force checkout, force pull, or an automatic stash.

If an update is available:

1. The remote branch is fetched without allowing interactive credential prompts.
2. The updater verifies that the current commit is an ancestor of the fetched remote commit.
3. If `requirements.txt` changed, the fetched requirements are installed with the currently running Python interpreter **before** project files are updated. If dependency installation fails, the code update is not applied.
4. Git performs `merge --ff-only` to the fetched commit.
5. The Python process restarts itself so the GUI runs only the new code.

## Data that is preserved

Untracked/ignored project data is not deleted or reset. This includes normal local profile, memory, run, and virtual-environment data such as:

- `memory/`
- `runs/`
- profile-owned memory and run folders
- `.venv/`
- local theme/background files that are not tracked by Git

Git itself will refuse the fast-forward if an untracked file would be overwritten by a tracked update.

## Cases that block automatic updating

The updater leaves the checkout unchanged when:

- tracked local edits exist;
- local and remote histories have diverged;
- the checkout is detached from a branch;
- `origin` does not point to `gladiatorgaming789-byte/Ai-plays-deltarune`;
- Git is unavailable;
- the network/fetch operation fails or times out; or
- a changed `requirements.txt` cannot be installed safely before the update.

The updater follows the branch that is currently checked out. During recovery work, `recovery/run19-hardening` therefore receives updates from that recovery branch. A checkout on `development` follows `development`. It does not silently switch branches or merge the draft recovery PR.

## Manual commands

Check without changing anything:

```powershell
python -m deltarune_agent.auto_update
```

Apply an available safe fast-forward immediately:

```powershell
python -m deltarune_agent.auto_update --apply
```

Temporarily disable startup updates in PowerShell:

```powershell
$env:DELTARUNE_AI_DISABLE_AUTO_UPDATE = "1"
python -m deltarune_agent gui
```

Remove that override later with:

```powershell
Remove-Item Env:DELTARUNE_AI_DISABLE_AUTO_UPDATE
```

## First installation of the updater

A copy of the project that predates the updater obviously cannot update itself yet. Pull or otherwise obtain the updater-enabled revision once. Every later GUI launch can then perform safe automatic updates on its own.
