# Save Profiles and Safe Testing Launcher

The desktop command now opens a profile launcher before the controller:

```powershell
python -m deltarune_agent gui
```

`Start AI GUI.bat` opens the same launcher.

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

When the launcher first sees real `memory` or `runs` directories in the project, it:

1. copies them into the active profile;
2. verifies every copied file by relative path and size;
3. writes a second backup under `migration-backups` in AppData;
4. removes the original project directories; and
5. creates repository-local directory links pointing at the selected profile.

The repository still sees `memory/` and `runs/` at their normal paths, so existing controller code and command-line options continue to work. GitHub Desktop branch switches do not replace the profile data because the links remain untracked and ignored.

## Profile actions

The launcher supports creating empty profiles, duplicating a profile (including its memory and runs), renaming profiles, deleting profiles, and opening a profile's AppData folder.

Close the controller before selecting another profile. A selected profile becomes the active target for both the GUI and later direct command-line runs from that repository folder.

## Branch and update safety

The launcher uses GitHub Desktop's bundled Git when regular `git` is not on PATH. It fetches `origin`, displays the current branch and installed revision, and warns before opening the controller when the checkout is:

- on a branch other than `agent/hierarchical-agent-improvements`;
- behind the remote development branch;
- diverged from origin; or
- unable to verify the remote state.

The controller window title keeps the profile, development-branch state, freshness, and agent revision visible during a run.

On first launch, the app also creates this untracked file in the repository:

```text
Start Deltarune Agent Safe.cmd
```

It is added to `.git/info/exclude`, so GitHub Desktop does not list it as a change, and it remains in the folder when branches switch.
