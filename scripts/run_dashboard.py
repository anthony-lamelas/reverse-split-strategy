import sys
import os

# Add src directory to PYTHONPATH
src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../src'))
if src_path not in sys.path:
    sys.path.insert(0, src_path)

# Import and run the dashboard
from split_strategy.ui.dashboard import run_dashboard

if __name__ == "__main__":
    run_dashboard()
