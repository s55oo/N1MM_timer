"""Entry point (also used by PyInstaller): python run_timer.py [--console | --check LOG.cbr]"""
import sys

from n1mm_timer.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
