import os
import sys

# Ensure root workspace directory is on sys.path so app, database, etc. import correctly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
