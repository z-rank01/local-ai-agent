"""The immutable v1 URL builder is a bundled dependency, not another endpoint."""
from pathlib import Path
import runpy

if __name__ == '__main__':
    runpy.run_path(str(Path(__file__).resolve().parents[2]/'harness-public-sources-v1/scripts/source_urls.py'),run_name='__main__')
