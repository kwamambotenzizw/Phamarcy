import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server as s


class ProductionSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = s.DB
        s.DB = Path(self.tmp.name) / 'empty.sqlite'
        self.env = patch.dict(os.environ, {'PHARMA_ENV': 'development'}, clear=False)
        self.env.start()
        os.environ.pop('PHARMA_SEED_DEMO', None)

    def tearDown(self):
        s.DB = self.old_db
        self.env.stop()
        self.tmp.cleanup()

    def test_default_initialization_creates_schema_without_users_or_sample_records(self):
        s.init()
        c = s.connect()
        try:
            for table in ('users', 'customers', 'products', 'batches', 'documents', 'payments', 'journals', 'assets'):
                self.assertEqual(c.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0, table)
        finally:
            c.close()

    def test_demo_seeding_is_rejected_for_production(self):
        with patch.dict(os.environ, {'PHARMA_ENV': 'production'}):
            with self.assertRaisesRegex(RuntimeError, 'forbidden in production'):
                s.init(seed_demo=True)
        self.assertFalse(s.DB.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
