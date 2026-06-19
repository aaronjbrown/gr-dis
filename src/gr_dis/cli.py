"""gr-dis command-line interface."""

from __future__ import annotations

import argparse
import sys

from pydantic import ValidationError


def _cmd_validate(args: argparse.Namespace) -> int:
    from gr_dis.engine.config import load_config

    try:
        load_config(args.config)
        print(f"Config OK: {args.config}")
        return 0
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except ValidationError as e:
        for err in e.errors():
            loc = " -> ".join(str(p) for p in err["loc"])
            print(f"error: {loc}: {err['msg']}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def _cmd_bridge(args: argparse.Namespace) -> int:
    import asyncio
    import logging

    from gr_dis.bridge.main import run_bridge
    from gr_dis.engine.config import load_config

    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValidationError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    log_level = getattr(logging, config.bridge.log_level.value, logging.INFO)
    logging.basicConfig(level=log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    import contextlib

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run_bridge(config))
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    import logging
    import threading

    from gr_dis.engine.config import load_config

    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValidationError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    log_level = getattr(logging, config.bridge.log_level.value, logging.INFO)
    logging.basicConfig(level=log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logger = logging.getLogger("gr_dis.run")

    # Bridge in a background thread (its own asyncio loop) unless suppressed.
    bridge_thread: threading.Thread | None = None
    bridge_loop_holder: dict[str, object] = {}
    if not args.no_bridge:
        import asyncio

        from gr_dis.bridge.main import run_bridge

        def _run_bridge() -> None:
            loop = asyncio.new_event_loop()
            bridge_loop_holder["loop"] = loop
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(run_bridge(config))
            except asyncio.CancelledError:
                pass
            finally:
                loop.close()

        bridge_thread = threading.Thread(target=_run_bridge, name="bridge", daemon=True)
        bridge_thread.start()

    # GR import is lazy — only fails here if the user actually tries to run
    # without gnuradio installed (i.e. outside the toolbox).
    try:
        from gr_dis.engine.capture import run_capture
    except ImportError as exc:
        print(
            f"error: gnuradio not available ({exc}); run inside the gr-dis toolbox",
            file=sys.stderr,
        )
        return 1

    rc = run_capture(config, capture_id=args.capture, source_file=args.source_file)

    # Tear down the bridge thread if we started one.
    if bridge_thread is not None:
        loop = bridge_loop_holder.get("loop")
        if loop is not None:
            import asyncio

            assert isinstance(loop, asyncio.AbstractEventLoop)
            for task in asyncio.all_tasks(loop):
                loop.call_soon_threadsafe(task.cancel)
        bridge_thread.join(timeout=5.0)
        if bridge_thread.is_alive():
            logger.warning("bridge thread did not exit within 5 s")

    return rc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gr-dis",
        description="GNU Radio → DIS radio bridge",
    )
    sub = parser.add_subparsers(dest="command")

    validate = sub.add_parser("validate", help="validate a config file")
    validate.add_argument("--config", required=True, metavar="PATH", help="path to YAML config")
    validate.set_defaults(func=_cmd_validate)

    bridge = sub.add_parser("bridge", help="run the bridge process (no GR capture)")
    bridge.add_argument("--config", required=True, metavar="PATH", help="path to YAML config")
    bridge.set_defaults(func=_cmd_bridge)

    run = sub.add_parser("run", help="run capture (+ bridge by default)")
    run.add_argument("--config", required=True, metavar="PATH", help="path to YAML config")
    run.add_argument(
        "--capture",
        default=None,
        metavar="ID",
        help="capture id to run (default: first capture in config)",
    )
    run.add_argument(
        "--source-file",
        default=None,
        metavar="PATH",
        help="play back a complex-float-32 IQ file instead of opening the SDR",
    )
    run.add_argument(
        "--no-bridge",
        action="store_true",
        help="do not start the bridge in this process group",
    )
    run.set_defaults(func=_cmd_run)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(0)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
