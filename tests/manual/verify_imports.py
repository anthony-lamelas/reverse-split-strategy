"""
Verify imports for the new package structure.
"""
import sys
import os
from pathlib import Path
import traceback

# Add src to path
current_dir = Path(__file__).resolve().parent
src_path = current_dir.parent / 'src'
sys.path.append(str(src_path))

log_file = current_dir / 'verify.log'

def log(msg):
    print(msg)
    with open(log_file, 'a') as f:
        f.write(msg + '\n')

with open(log_file, 'w') as f:
    f.write(f"Added {src_path} to sys.path\n")

try:
    log("Importing config...")
    from split_strategy import config
    log("  OK")
    
    log("Importing database...")
    from split_strategy import database
    log("  OK")
    
    log("Importing edgar.client...")
    from split_strategy.edgar import client
    log("  OK")
    
    log("Importing edgar.parsing...")
    from split_strategy.edgar import parsing
    log("  OK")
    
    log("Importing edgar.scoring...")
    from split_strategy.edgar import scoring
    log("  OK")
    
    log("Importing edgar.processing...")
    from split_strategy.edgar import processing
    log("  OK")
    
    log("Importing scrapers.stockanalysis...")
    from split_strategy.scrapers import stockanalysis
    log("  OK")
    
    log("Importing scrapers.runner...")
    from split_strategy.scrapers import runner
    log("  OK")
    
    log("\nAll imports successful!")

except Exception as e:
    log(f"\nIMPORT ERROR: {e}")
    with open(log_file, 'a') as f:
        traceback.print_exc(file=f)
    sys.exit(1)
