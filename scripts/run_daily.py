"""Daily UI backend; optional stock services are controlled from the Web panel."""
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
os.chdir(root)
os.environ['DAILY_SERVICES'] = '1'

if __name__ == '__main__':
    import uvicorn
    uvicorn.run('bff.app:app', host='127.0.0.1', port=9510)
