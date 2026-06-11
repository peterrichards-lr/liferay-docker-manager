#!/usr/bin/env python3
import sys
from pathlib import Path

# Add project root to sys.path so ldm_core can be imported without installation
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from ldm_core.constants import VERSION  # noqa: E402


def main():
    # Print the exact version without trailing newlines to avoid bash script injection issues
    print(VERSION, end="")


if __name__ == "__main__":
    main()
