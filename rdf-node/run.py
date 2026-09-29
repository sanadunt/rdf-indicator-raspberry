#!/usr/bin/env python3
"""Source distribution launcher; no system/Conda packages are modified."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'vendor')]
from rdf_node.cli import main
if __name__ == '__main__':
    main()
