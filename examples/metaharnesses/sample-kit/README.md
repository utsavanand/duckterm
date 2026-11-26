# sample-kit

One of every component kind in the smallest form that still does something.
Try it on a scratch repository:

    duckterm kit validate examples/skill-kits/sample-kit
    duckterm kit plan    examples/skill-kits/sample-kit --project /tmp/scratch --harness claude-code --harness codex
    duckterm kit install examples/skill-kits/sample-kit --project /tmp/scratch --harness claude-code
    duckterm kit verify  --project /tmp/scratch
    duckterm kit uninstall sample-kit --project /tmp/scratch

Nothing in the kit runs at install time. The two hook scripts run only when
the harness fires the event, through the shim the installer writes next to them.
