#!/usr/bin/env python3
"""Collect one due measurement using the existing official Ookla binary."""
import json
import signal
from network_speed import collect

def interrupted(*_):
    raise InterruptedError('Collector stopped')

if __name__ == '__main__':
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    print(json.dumps(collect()), flush=True)
