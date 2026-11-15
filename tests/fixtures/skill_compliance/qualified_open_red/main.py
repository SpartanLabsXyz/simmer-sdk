import argparse
import os
p = argparse.ArgumentParser()
p.add_argument("--live", action="store_true")
key = os.getenv("FIXTURE_KEY")
url = "https://api.example.com/data"
import builtins
import os
builtins.open("/tmp/state.json", "w")
os.open("../state.json", os.O_WRONLY | os.O_CREAT)
