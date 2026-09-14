"""Local IN1 BFF, isolated from existing workspace and conversations."""
import os
import sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
os.chdir(root)
for key, value in {
    'WORKSPACE_PATH': str(root / 'data/in1/workspace'),
    'DB_PATH': str(root / 'data/in1/conversations.db'),
    'LOG_PATH': str(root / 'data/in1/logs/audit.jsonl'),
    'WORKSPACE_CLOUD_ALLOWED': 'true',
    'SKILL_FILES_URL': 'http://127.0.0.1:19101',
    'SKILL_RUNNER_URL': 'http://127.0.0.1:19102',
    'SKILL_WEBSEARCH_URL': 'http://127.0.0.1:19103',
    'DEFAULT_MODEL': 'qwen:qwen3.5-flash',
    'MODEL_CALL_LIMIT': '200',  # IN1 用 30；2026-09-07 用户批准 +30 供 IN2；2026-09-08 翻倍至 120 供 IN3；2026-09-10 批准至 200 供 IN3.H 人工验收（含真实研究闭环与重试余量）
    'TOOL_TIER': 'all',
    'ENABLE_WEBSEARCH': 'true',
}.items():
    os.environ[key] = value
import uvicorn
uvicorn.run('bff.app:app', host='127.0.0.1', port=9510)
