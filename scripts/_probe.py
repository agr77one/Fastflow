import sys, os
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])) if False else "", ""))
# import paths the way the app does
import paths as p
import json
out = {
  "frozen": getattr(sys, "frozen", False),
  "executable": sys.executable,
  "MEIPASS": getattr(sys, "_MEIPASS", None),
  "paths_file": getattr(p, "__file__", None),
  "SCRIPTS_DIR": str(p.SCRIPTS_DIR),
  "INSTALL_MODE": p.INSTALL_MODE,
  "APP_DIR": str(p.APP_DIR),
  "USER_ROOT": str(p.USER_ROOT),
  "SETUP_DIR": str(p.SETUP_DIR),
  "CONFIG_SEED_FILE": str(p.CONFIG_SEED_FILE),
  "CONFIG_SEED_EXISTS": p.CONFIG_SEED_FILE.exists(),
}
print("PROBE_JSON=" + json.dumps(out))
