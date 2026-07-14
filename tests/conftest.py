import sys
import os

# Ensure project root is on sys.path so tests can import logger, scanner, kalshi, etc.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
