#!/usr/bin/env python3
import sys
import arrow
import time
i = 0
while True:
    print(str(arrow.get()))
    sys.stderr.write("ERROR: " + str(arrow.get()) + "\n")
    time.sleep(0.5)
    i += 1

    if i > 350:
        break

    sys.stdout.flush()
    sys.stderr.flush()