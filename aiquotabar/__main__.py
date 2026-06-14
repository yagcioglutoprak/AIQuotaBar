"""AIQuotaBar entry point."""

import sys


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("--json", "-j"):
        import json
        import os
        from aiquotabar.config import WIDGET_CACHE_FILE
        if os.path.exists(WIDGET_CACHE_FILE):
            try:
                with open(WIDGET_CACHE_FILE, "r") as f:
                    data = json.load(f)
                print(json.dumps(data, indent=2))
                sys.exit(0)
            except Exception as e:
                print(json.dumps({"error": f"Failed to read cache file: {e}"}))
                sys.exit(1)
        else:
            print(json.dumps({"error": "No usage data collected yet. Please start the app first."}))
            sys.exit(1)
    elif len(sys.argv) > 1 and sys.argv[1] in ("--history", "-H"):
        from aiquotabar.history import cli_history
        cli_history()
    else:
        from aiquotabar.ui import ClaudeBar
        ClaudeBar().run()


if __name__ == "__main__":
    main()
