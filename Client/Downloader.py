#!/usr/bin/env python3
import runpy
if __name__ == "__main__":
    # Run and exit cleanly; run_path returns a dict which we ignore
    runpy.run_path("src/Downloader.py", run_name="__main__")
