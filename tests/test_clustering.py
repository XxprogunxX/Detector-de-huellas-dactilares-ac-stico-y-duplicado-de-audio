"""
Unit tests for DisjointSet clustering and group generation.
"""

import unittest
from core.models import AudioTrack, DuplicateType, FileAction
from core.clustering import cluster_duplicates, DisjointSet


class TestClustering(unittest.TestCase):
    def test_disjoint_set_union_find(self):
        ds = DisjointSet()
        ds.union("a", "b")
        ds.union("b", "c")
        self.assertEqual(ds.find("a"), ds.find("c"))
        self.assertNotEqual(ds.find("a"), ds.find("d"))

    def test_exact_hash_clustering(self):
        t1 = AudioTrack(filepath="path/song1.mp3", sha256="same_hash", filesize=1000, duration=200.0)
        t2 = AudioTrack(filepath="path/song1_copy.mp3", sha256="same_hash", filesize=1000, duration=200.0)
        t3 = AudioTrack(filepath="path/other.mp3", sha256="diff_hash", filesize=1500, duration=200.0)

        groups = cluster_duplicates([t1, t2, t3])
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].tracks), 2)
        self.assertEqual(groups[0].primary_type, DuplicateType.EXACT_HASH)


if __name__ == "__main__":
    unittest.main()
