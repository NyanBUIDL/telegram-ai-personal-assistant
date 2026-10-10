# Private Windows owner preview: install, upgrade and remove

This installer is for the reviewed private owner preview on Windows 11 x64. It is unsigned: there is no verified application publisher. Its compiler's signature does not sign the assistant. Windows 10, ARM64, a standard account on a clean machine, reboot startup and physical DPI coverage remain unverified. Public distribution still requires the project/font/Qt/source-notice and signing gates.

Use only the exact installer SHA-256 recorded in its reviewed build manifest. Do not change Windows Application Control, Smart App Control or antivirus policy to run a blocked artifact. One synthetic test installer was blocked on the development host; that observation does not establish the outcome for a future final candidate.

## Install

Close the assistant from its tray menu and wait for active work to stop. Run the installer as your normal Windows account. It requests no elevation and installs only under `%LOCALAPPDATA%\Programs\TelegramAIPersonalAssistant`; custom directories are refused. The start-menu shortcut is for your account. “Mở ứng dụng khi tôi đăng nhập vào Windows” is unchecked by default; enable it only if wanted.

The package includes its Python/Qt runtime and dashboard. You do not need to install Python, Node or MySQL. Launch the assistant from its start-menu shortcut after setup exits. Setup does not launch the assistant, open a Telegram account, read credentials, or run profile migration. The native launcher handles setup and the existing backup/migration/recovery flow on first launch. Enter secrets only in the assistant's native dialogs.

Mutable files remain separately under `%LOCALAPPDATA%\TelegramAIPersonalAssistant`: config, SQLite database, Telegram sessions, vectors, downloaded media, logs and backups. Installer preparation may create only the protected `.desktop-control\runtime.lock` used to exclude the runtime while application files change. The normal launcher claims an otherwise empty control-only root before initializing a fresh profile.

## Upgrade and recovery

Create and verify a backup using the existing native maintenance flow before upgrading an established SQLite profile. Exit the assistant. The installer refuses an active runtime instead of killing it; closing the visible window may merely hide it in the tray. Retry only after the tray exit finishes.

The installer checks the default current-user profile and refuses linked, inaccessible, unsupported or custom profile targets. Existing MySQL settings are preserved and refused. This preview is SQLite-only: changing a legacy backend field or removing its data is not a migration. Keep a separate backup of the old profile and use an explicitly planned import/migration outside this installer.

Application payloads live in `versions\<version>-<P01-manifest-hash-prefix>`. An upgrade copies a complete new payload and validates its helper before changing shortcuts. Earlier payloads remain until uninstall, so an interrupted copy does not overwrite them. Re-run the same reviewed installer to recover an interrupted installation. This may consume additional disk space; do not manually delete files while setup or the assistant runs.

If the current payload/helper is missing or changed, uninstall refuses instead of guessing. Reinstall the same reviewed package first, then retry uninstall. A retained old executable is not authorization to downgrade an already migrated database: use the existing verified backup/restore procedure. Power-loss/disk-full rollback and final-candidate upgrade recovery remain Pending until tested on that exact artifact.

## Uninstall

Exit the assistant, then use Windows Installed apps or the application's uninstaller. It removes application files and its shortcuts for the current account. It preserves configuration, databases, sessions, vectors, media, backups, logs and credential-store entries. It does not remove shared/global runtimes or contact MySQL.

There is no automated “purge data” option in this private preview. The historical `opt_in_purge_only` QA case verifies the absence of an automatic purge path. Any later data deletion requires separate explicit authorization and its own checks.

## Build and evidence

After the reviewed source freeze, build P01 from a clean checkout, then run from the checkout root:

```powershell
./scripts/build-installer.ps1 -Compiler 'E:/ChatGPT-Temp/p02-compiler-20261010/tools/ISCC.exe'
```

The wrapper accepts only P01's complete clean `private-preview-candidate` manifest. It verifies version, source commit and every source/payload inventory member/hash before and after compilation. It rejects dirty preflights, linked trees, missing/extra/changed files, and an unexpected compiler or signing status. Outputs are under `build/windows-installer`; its manifest records the installer hash, P01 manifest hash, source commit, compiler inventory and actual `NotSigned` observation. No signing command is invoked. A future signing step requires an authorized certificate and a new reviewed manifest/checksum.

The pinned compiler is [Inno Setup 6.7.3](https://github.com/jrsoftware/issrc/releases/tag/is-6_7_3), official asset SHA-256 `9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732`. Acquisition observed a valid Pyrsys B.V. signature issued by Sectigo Public Code Signing CA R36. Keep its supplied `license.txt`; the compiler is scoped to a private portable tools directory, without associations or global PATH changes.

`tests/windows/test_installer_manifest.ps1` exercises source validation without changing candidate output. `tests/windows/test_install_upgrade_uninstall.ps1` compiles inert, explicitly synthetic fixtures under a fresh owned `.test-temp` directory with a separate AppId and synthetic shortcut/profile locations. It never proves real Qt startup, database migration, credential-store behavior, clean-machine support, or release readiness. An OS launch block leaves installation cases blocked/Pending, not passed. Final P01/P02 compilation, installed launcher diagnostics and owner-preview acceptance require the root's exact-candidate checkpoint and independent review.
