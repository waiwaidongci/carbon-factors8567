import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix='carbon-test-')
os.environ.setdefault('DATABASE_URL', 'sqlite:///' + os.path.join(_tmp, 'test.db'))
os.environ.setdefault('JWT_SECRET', 'test-secret')
os.environ.setdefault('REDIS_URL', 'redis://localhost:6379/15')
os.environ.setdefault('PROJECT_CONFIG', str(ROOT / 'project.json'))
os.environ.setdefault('SEED_DEMO', 'false')
