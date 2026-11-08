"""Package installed Linux wheels and shared terrain code for AWS Lambda."""
import sys
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

here = Path(__file__).resolve().parent
libs = Path(sys.argv[1])
with ZipFile(sys.argv[2], "w", ZIP_DEFLATED) as archive:
    for path in libs.rglob("*"):
        rel = path.relative_to(libs)
        if path.is_file() and not {"__pycache__", "bin"}.intersection(rel.parts):
            archive.write(path, rel)
    for name in ["terrain", "terrain_job"]:
        archive.write(here.parents[1] / "server" / (name + ".py"), "server/" + name + ".py")
    archive.writestr("server/__init__.py", "")
    archive.write(here / "worker.py", "worker.py")
    print("Uncompressed bytes:", sum(f.file_size for f in archive.infolist()))
