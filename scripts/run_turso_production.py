"""Render production entry: read-only Turso checks, then Discord bot.

Explicit production opt-in is required. No staging probe runs here.
"""
import logging
import sys


def main():
    from scripts.verify_turso_production import verify
    try:
        verify()
    except Exception:
        logging.exception('TURSO_PRODUCTION_PRECHECK_FAILED: Discord login blocked')
        return 1
    from main import main as run_bot
    run_bot()
    return 0


if __name__ == '__main__':
    sys.exit(main())
