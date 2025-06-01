#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "pillow",
#   "tqdm",
#   "mutagen",
# ]
# ///

"""
NLE-Agnostic Timeline Generator

Creates timeline projects for multiple NLEs (Final Cut Pro, DaVinci Resolve, etc.)
by building an intermediate representation of photo timelines synchronized to beats.
"""

from __future__ import annotations

from beatliner import main


if __name__ == "__main__":
    main()
