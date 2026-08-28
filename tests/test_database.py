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
        self.db.close()
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
        
        self.assertEqual(t.fake_lossless_confidence, 1.0)
        self.assertEqual(t.spectral_cutoff, 15800.0)
        migrated_db.close()

    def test_database_helper_methods(self):
        track1 = AudioTrack(
            filepath="/music/rock/song1.mp3",
            filesize=3500000,
            mtime=1620000000.0,
            format="MP3",
            bitrate=320,
            title="Song 1",
            artist="Artist A"
        )
        track2 = AudioTrack(
            filepath="/music/rock/song2.flac",
            filesize=25000000,
            mtime=1620000100.0,
            format="FLAC",
            bitrate=900,
            is_lossless=True,
            title="Song 2",
            artist="Artist B"
        )
        self.db.upsert_tracks_batch([track1, track2])

        # Test count
        self.assertEqual(self.db.get_total_tracks_count(), 2)

        # Test format stats
        stats = self.db.get_format_statistics()
        self.assertIn("MP3", stats)
        self.assertIn("FLAC", stats)
        self.assertEqual(stats["MP3"]["count"], 1)
        self.assertEqual(stats["FLAC"]["count"], 1)

        # Test get_all_tracks
        tracks = self.db.get_all_tracks()
        self.assertEqual(len(tracks), 2)

        # Test db size and vacuum
        size = self.db.get_database_size_bytes()
        self.assertGreater(size, 0)
        self.db.vacuum_database()

        # Test clear
        self.db.clear_database()
        self.assertEqual(self.db.get_total_tracks_count(), 0)

if __name__ == '__main__':
    unittest.main()

