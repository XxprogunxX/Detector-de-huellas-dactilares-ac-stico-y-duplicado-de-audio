import unittest
import time
from core.models import AudioTrack
from core.clustering import cluster_duplicates

class TestClusteringPerformance(unittest.TestCase):
    def test_lsh_performance(self):
        """
        Tests that the LSH pre-filter prevents an O(N^2) explosion of candidate pairs.
        With 2000 tracks, a naive approach does ~2,000,000 comparisons.
        With LSH, it should do almost 0 if fingerprints don't collide.
        """
        tracks = []
        for i in range(2000):
            # Generate unique fingerprints to ensure they don't collide in LSH
            fp = [i] * 500 
            t = AudioTrack(
                filepath=f"path/song{i}.mp3", 
                duration=100.0 + (i % 10), 
                fingerprint_raw=fp
            )
            tracks.append(t)
            
        start_time = time.time()
        groups = cluster_duplicates(tracks)
        end_time = time.time()
        
        # If O(N^2) comparisons were executed, it would take many seconds.
        # It should complete in less than 2 seconds with an efficient LSH.
        self.assertLess(end_time - start_time, 2.0, "Clustering took too long! LSH pre-filter might be failing.")
        self.assertEqual(len(groups), 0)

if __name__ == '__main__':
    unittest.main()
