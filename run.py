#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""便捷入口: python run.py ... 等价于 python -m devboard_toolkit ..."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from devboard_toolkit.cli import main

if __name__ == "__main__":
    sys.exit(main())
