#!/usr/bin/python3
import sys
import time

seconds = None

if len(sys.argv) > 1:
    seconds = int(sys.argv[1])

for x in range(seconds or 60):
    print(f"stdout {x}", file=sys.stdout)
    print(f"stderr {x}", file=sys.stderr)
    time.sleep(0.5)
