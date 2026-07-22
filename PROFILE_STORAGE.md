# Save Profiles and Integrated Build Safety

The desktop command opens one controller window:

```powershell
python -m deltarune_agent gui
```

`Start AI GUI.bat` opens the same integrated GUI. The main window contains two tabs:

- **Controller** — the live map, run controls, decisions, and telemetry;
- **Profiles & Build** — save-profile management and development-version checks.

There is no separate profile-launcher window.

## AppData storage

On Windows, profiles are stored under:

```text
%LOCALAPPDATA%\DeltaruneAgent\profiles\<profile-id>\
├── memory\
└── runs\
```

The profile name is display metadata. A generated ID is used for the directory so renaming a profile cannot break its path.

Set `DELTARUNE_AGENT_DATA_DIR` to use a different data root while developing or testing.

## First-launch migration

When the integrated GUI first sees real `memory` or `runs` directories in the project, it:

1. copies them into the active profile;
2. verifies every copied file by relative path and size;
3. writes a second backup under `migration-backups` in AppData;
4. removes the original project directories; and
5. creates repository-local directory links pointing at the selected profile.

The repository still sees `memory/` and `runs/` at their normal paths, so existing controller code and command-line options continue to work. GitHub Desktop branch switches do not replace the profile data because the links remain untracked and ignored.

## Profile actions

The **Profiles & Build** tab supports creating empty profiles, duplicating a profile (including its memory and runs), renaming profiles, deleting profiles, and opening a profile's AppData folder.

Profile switching is disabled while the AI is running. After a profile changes, the controller reloads that profile's learned navigation and remembered room views immediately.

## Branch and update safety

The **Profiles & Build** tab uses GitHub Desktop's bundled Git when regular `git` is not on PATH. It fetches `origin`, displays the current branch and installed revision, and warns before starting the AI when the checkout is:

- on a branch other than `development`;
- behind the remote development branch;
- diverged from origin; or
- unable to verify the remote state.

The active profile, development-branch state, freshness, and agent revision remain visible in the main GUI header and window title while testing.

The persistent untracked shortcut remains supported:

```text
Start Deltarune Agent Safe.cmd
```

It verifies the checkout before opening the same integrated GUI. It is added to `.git/info/exclude`, so GitHub Desktop does not list it as a change, and it remains in the folder when branches switch.
