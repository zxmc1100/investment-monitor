"""`.venv/bin/python -m monitor <command> [args]` — init, serve, export, plus a local add-on's commands
(monitor.plugins). `--help` lists them."""
import sys

HEAD = ".venv/bin/python -m monitor <command> [args]      (e.g. .venv/bin/python -m monitor init)"
CORE = {"init": "create input/ from examples/ (portfolio, interest, settings; never overwrites)",
        "serve": "terminal server on http://localhost:8000 (auto-reload)",
        "export": "public static snapshot → docs/"}


def usage(commands: dict) -> str:
    """The help: the core's commands, then a line per command a local add-on adds."""
    rows = {**CORE, **{name: help for name, (_main, help) in commands.items()}}
    return HEAD + "\n\n" + "".join(f"  {name:<14} {help}\n" for name, help in rows.items())


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    from monitor import config, plugins
    commands = plugins.commands()             # a local add-on first: it may add settings keys
    if config.SETTINGS_ERROR:                 # defaults are in use for the bad keys: say so, once
        print(config.SETTINGS_ERROR, file=sys.stderr)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(usage(commands))
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "init":
        from monitor.init import main as init_main
        return init_main(rest)
    if cmd == "serve":
        from monitor.server.run import serve
        return serve(rest)
    if cmd == "export":
        from monitor.server.export import main as export_main
        return export_main(rest)
    if cmd in commands:
        return commands[cmd][0](rest)
    print(f"unknown command: {cmd}\n{usage(commands)}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
