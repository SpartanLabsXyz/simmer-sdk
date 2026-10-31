import argparse
import os
p = argparse.ArgumentParser()
p.add_argument("--live", action="store_true")
key = os.getenv("FIXTURE_KEY")
url = "https://api.example.com/data"
namespace = "http://www.w3.org/2005/Atom"; url = "http://www.w3.org/network"; requests.get(url)
