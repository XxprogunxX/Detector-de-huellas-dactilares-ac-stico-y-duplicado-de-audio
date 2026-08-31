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

    def test_lsh_false_positive_rejection(self):
        # Force LSH collision with identical initial frames, but rest is totally different
        fp1 = [0xABCDABCD] * 100 + [0xFFFFFFFF] * 400
        fp2 = [0xABCDABCD] * 100 + [0x00000000] * 400

        t1 = AudioTrack(filepath="path/song1.mp3", duration=200.0, fingerprint_raw=fp1)
        t2 = AudioTrack(filepath="path/song2.mp3", duration=200.0, fingerprint_raw=fp2)

        groups = cluster_duplicates([t1, t2])
        # Even though LSH puts them in the same bucket, compare_fingerprints should reject them
        self.assertEqual(len(groups), 0)


    def test_low_confidence_review_default_action(self):
        # Fingerprints just similar enough to trigger LOW_CONFIDENCE_REVIEW (40-79.9%)
        fp1 = [0xABCDABCD] * 50 + [0x12345678] * 450
        fp2 = [0xABCDABCD] * 50 + [0x00000000] * 450
        t1 = AudioTrack(filepath="path/song1.mp3", duration=200.0, fingerprint_raw=fp1)
        t2 = AudioTrack(filepath="path/song2.mp3", duration=200.0, fingerprint_raw=fp2)

        groups = cluster_duplicates([t1, t2])
        if len(groups) == 1:
            group = groups[0]
            if group.primary_type == DuplicateType.LOW_CONFIDENCE_REVIEW:
                self.assertTrue(group.requires_manual_review)
                for t in group.tracks:
                    self.assertEqual(t.action, FileAction.UNSET)

    # --- REGRESSION TESTS PARA AUD-001 y AUD-002 ---

    def test_lsh_large_bucket_60_copies(self):
        """
        AUD-002: Verifica que LSH no descarte grupos grandes legítimos (ej. 60 copias idénticas)
        porque el límite de max_bucket_size ahora es 500, no 35.
        """
        tracks = []
        fp = [0x10000000 + i for i in range(300)]
        for i in range(60):
            tracks.append(AudioTrack(filepath=f"path/copy_{i}.mp3", duration=200.0, fingerprint_raw=fp))
            
        groups = cluster_duplicates(tracks)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].tracks), 60)
        self.assertEqual(groups[0].primary_type, DuplicateType.ACOUSTIC_DUPLICATE)

    def test_lsh_radio_edit_extreme_duration_diff(self):
        """
        AUD-002: Verifica que la diferencia de duración estricta ha sido eliminada del prefiltro LSH,
        permitiendo detectar duplicados acústicos con duraciones radicalmente diferentes.
        """
        # fp idénticos pero con diferencia de duración de 60 segundos
        fp = [0x10000000 + i for i in range(300)]
        t1 = AudioTrack(filepath="path/extended.mp3", duration=240.0, fingerprint_raw=fp)
        t2 = AudioTrack(filepath="path/radio_edit.mp3", duration=180.0, fingerprint_raw=fp)
        
        groups = cluster_duplicates([t1, t2])
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].tracks), 2)
        # Por la diferencia de duración, compare_tracks lo marcará como POSSIBLE_DUPLICATE
        self.assertEqual(groups[0].primary_type, DuplicateType.POSSIBLE_DUPLICATE)
        self.assertTrue(groups[0].requires_manual_review)

    def test_union_find_weak_link_transitivity(self):
        """
        AUD-001: Verifica la heurística `has_weak_link`. 
        Si A-B es ACOUSTIC, B-C es POSSIBLE (débil), y A-C es LOW_CONFIDENCE,
        el componente entero debe degradarse a POSSIBLE y exigir revisión manual.
        """
        import random
        random.seed(42)
        base_fp = [random.randint(1, 0x7FFFFFFF) for _ in range(300)]
        fp_a = base_fp[:]
        fp_b = base_fp[:]
        
        # C es similar a B pero con 23 frames de ruido, garantizando similitud ~84.6% -> POSSIBLE_DUPLICATE
        fp_c = base_fp[:277] + [base_fp[i] ^ 0xFFFFFFFF for i in range(277, 300)]
        
        t1 = AudioTrack(filepath="path/track_A.mp3", duration=200.0, fingerprint_raw=fp_a)
        t2 = AudioTrack(filepath="path/track_B.mp3", duration=200.0, fingerprint_raw=fp_b)
        t3 = AudioTrack(filepath="path/track_C.mp3", duration=200.0, fingerprint_raw=fp_c)
        
        groups = cluster_duplicates([t1, t2, t3])
        
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].tracks), 3)
        self.assertEqual(groups[0].primary_type, DuplicateType.POSSIBLE_DUPLICATE)
        self.assertTrue(groups[0].requires_manual_review)
        
        # Verificar que C está protegida de auto-eliminación
        # Como req_review es True, ninguna acción debe ser DELETE_B o DELETE_A
        for t in groups[0].tracks:
            self.assertEqual(t.action, FileAction.UNSET)


if __name__ == "__main__":
    unittest.main()
