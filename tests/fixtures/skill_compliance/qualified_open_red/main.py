import argparse
import os
p = argparse.ArgumentParser()
p.add_argument("--live", action="store_true")
key = os.getenv("FIXTURE_KEY")
url = "https://api.example.com/data"
import builtins
builtins.open("/tmp/outside-state.json", "w")
os.open("../outside-state.json", os.O_WRONLY | os.O_CREAT)
