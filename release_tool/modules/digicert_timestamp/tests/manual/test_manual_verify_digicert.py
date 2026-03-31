import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent

data_file = HERE / "manifest-v2.0.22.json"
tsr_file = HERE / "manifest-v2.0.22.json.tsr"
verify_script = HERE.parent.parent / "verify_tsr.py"

result = subprocess.run(
    [sys.executable, str(verify_script), str(data_file), str(tsr_file), "sha256"],
    check=False,
)
sys.exit(result.returncode)
