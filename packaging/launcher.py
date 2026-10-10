import sys

if any(argument.startswith("--installer-") for argument in sys.argv[1:]):
    if len(sys.argv) != 2 or sys.argv[1] not in {"--installer-check", "--installer-prepare"}:
        sys.exit(2)
    from tg_assistant.desktop.installer_check import main

    sys.exit(main(prepare=sys.argv[1] == "--installer-prepare"))

if len(sys.argv) == 3 and sys.argv[1] == "--diagnostics":
    from tg_assistant.desktop.diagnostics import run_diagnostics

    sys.exit(run_diagnostics(sys.argv[2]))

from tg_assistant.desktop.app import main

sys.exit(main())
