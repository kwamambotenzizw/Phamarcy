"""Stage frontend assets in Vercel's static public directory."""
from pathlib import Path
import shutil

root = Path(__file__).parent
source = root / "web"
target = root / "public"
if target.exists():
    shutil.rmtree(target)
shutil.copytree(source, target)
