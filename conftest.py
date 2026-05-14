import sys
from pathlib import Path

# Add backend directory to Python path so imports like "from seeds.xxx" work
sys.path.insert(0, str(Path(__file__).resolve().parent))
