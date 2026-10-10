import sys

if len(sys.argv) == 3 and sys.argv[1] == "--diagnostics":
    from tg_assistant.desktop.diagnostics import run_diagnostics

    sys.exit(run_diagnostics(sys.argv[2]))

from tg_assistant.desktop.app import main

sys.exit(main())
