import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "apps/api"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "desk.settings")
os.environ["REVOPS_DESK"] = "1"

if __name__ == "__main__":
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)
