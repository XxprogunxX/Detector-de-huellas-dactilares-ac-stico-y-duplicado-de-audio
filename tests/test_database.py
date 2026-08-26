import unittest
import os
import tempfile
import sqlite3
from core.database import Database
from core.models import AudioTrack

class TestDatabaseCache(unittest.TestCase):
    def setUp(self):
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".sqlite")
        self.db = Database(db_path=self.temp_db_path)

    def tearDown(self):
        os.close(self.temp_db_fd)
        if os.path.exists(self.temp_db_path):
            os.remove(self.temp_db_path)

    def test_legacy_schema_migration_and_lazy_load(self):
        """
        Verify that a track saved with the old schema (using is_fake_lossless)
        is correctly migrated and lazy-loaded via get_lightweight_cache_lookup.
        """
        # 1. Database connection is created on demand, no need to close

        
        # 2. Simulate old schema (create table without fake_lossless_confidence, but with is_fake_lossless)
        conn = sqlite3.connect(self.temp_db_path)
        try:
            conn.execute("DROP TABLE IF EXISTS tracks;")
            conn.execute("""
                CREATE TABLE tracks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filepath TEXT UNIQUE NOT NULL,
                    filesize INTEGER NOT NULL,
                    mtime REAL NOT NULL,
                    sha256 TEXT,
                    audio_hash TEXT,
                    duration REAL,
                    format TEXT,
                    bitrate INTEGER,
                    samplerate INTEGER,
                    channels INTEGER,
                    bit_depth INTEGER,
                    is_lossless INTEGER,
                    spectral_cutoff REAL,
                    is_fake_lossless INTEGER,
                    quality_score REAL,
                    quality_details TEXT,
                    fingerprint BLOB,
                    title TEXT,
                    artist TEXT,
                    album TEXT,
                    last_scanned TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            # Insert dummy track with legacy schema (is_fake_lossless = 1 means it is fake)
            conn.execute("""
                INSERT INTO tracks (
                    filepath, filesize, mtime, duration, format, 
                    bitrate, samplerate, channels, bit_depth, is_lossless, 
                    spectral_cutoff, is_fake_lossless, quality_score, fingerprint
                ) VALUES (
                    '/fake/path/song.flac', 5000000, 1600000000.0, 180.0, 'FLAC',
                    900, 44100, 2, 16, 1,
                    15800.0, 1, 40.0, NULL
                )
            """)
            conn.commit()
        finally:
            conn.close()

        # 3. Instantiate Database again (this should trigger migration logic in _initialize_db)
        # Note: the migration logic is "ALTER TABLE tracks RENAME COLUMN is_fake_lossless TO fake_lossless_confidence"
        migrated_db = Database(db_path=self.temp_db_path)
        
        # 4. Verify lightweight cache can see it
        lightweight_cache = migrated_db.get_lightweight_cache_lookup()
        self.assertIn('/fake/path/song.flac', lightweight_cache)
        size, mtime = lightweight_cache['/fake/path/song.flac']
        self.assertEqual(size, 5000000)
        self.assertEqual(mtime, 1600000000.0)

        # 5. Verify lazy load can fetch the full track properly
        tracks = migrated_db.get_tracks_for_files(['/fake/path/song.flac'])
        self.assertEqual(len(tracks), 1)
        t = tracks[0]
        self.assertEqual(t.filepath, '/fake/path/song.flac')
        
        # Since 'is_fake_lossless' (1) was renamed to 'fake_lossless_confidence', 
        # it will be read as 1.0 (float) which is fine for backward compatibility testing,
        # or we just need to ensure the schema didn't crash.
        self.assertEqual(t.fake_lossless_confidence, 1.0)
        self.assertEqual(t.spectral_cutoff, 15800.0)

if __name__ == '__main__':
    unittest.main()
