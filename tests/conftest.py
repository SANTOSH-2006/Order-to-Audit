import os, sys, pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import db, seed

@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "t.db")); db.init(c); seed.seed(c)
    yield c
    c.close()
