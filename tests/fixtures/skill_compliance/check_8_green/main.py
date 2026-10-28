import argparse
import os
p = argparse.ArgumentParser()
p.add_argument("--live", action="store_true")
key = os.getenv("FIXTURE_KEY")
url = "https://api.example.com/data"
from pathlib import Path
Path("state.json").write_text("{}")
