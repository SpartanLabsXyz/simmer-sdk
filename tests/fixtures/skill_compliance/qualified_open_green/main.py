import argparse
import os
p = argparse.ArgumentParser()
p.add_argument("--live", action="store_true")
key = os.getenv("FIXTURE_KEY")
url = "https://api.example.com/data"
import builtins
builtins.open("/tmp/read-only.json", "r")
builtins.open("state.json", "w")
os.open("/tmp/read-only.json", os.O_RDONLY)
os.open(path="/tmp/read-only.json", flags=os.O_RDONLY | os.O_CLOEXEC)
os.open("../read-only.json", 0)
os.open("state.json", os.O_WRONLY | os.O_CREAT)
