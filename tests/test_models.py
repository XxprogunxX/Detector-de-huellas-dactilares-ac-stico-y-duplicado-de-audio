import unittest
from core.models import AudioTrack, FileAction

class TestModels(unittest.TestCase):
    def test_from_dict_preserves_fingerprint_raw(self):
        """Bug regression: ensure from_dict keeps fingerprint_raw if provided."""
        
        # 1. El escenario que el framework de validación usaba (y fallaba): 
        # _process_audio_worker devuelve el raw en el diccionario.
        data_from_worker = {
            "filepath": "/dummy/path.wav",
            "fingerprint_raw": [1, 2, 3, 4, 5],
            "duration": 45.0
        }
        
        track = AudioTrack.from_dict(data_from_worker)
        
        # 2. Afirmar que el arreglo NO fue sobrescrito con []
        self.assertEqual(track.fingerprint_raw, [1, 2, 3, 4, 5], 
                         "from_dict debe preservar fingerprint_raw si existe en el diccionario")
        
        # 3. Afirmar que el comportamiento de producción no cambia:
        # En DB, to_dict() / from_dict() no mandan la huella en el dict principal
        # (se carga desde la tabla BLOB).
        data_from_db = {
            "filepath": "/dummy/path.wav",
            "duration": 45.0
        }
        
        track_db = AudioTrack.from_dict(data_from_db)
        
        # Si no existe, sigue por defecto como arreglo vacío (seguro para el resto del sistema).
        self.assertEqual(track_db.fingerprint_raw, [], 
                         "from_dict debe usar un arreglo vacío por defecto si la huella no viene en el dicc")

    def test_requires_manual_review_roundtrip(self):
        """TEST A: requires_manual_review=True serializar -> deserializar conserva True."""
        from core.models import DuplicateGroup, DuplicateType
        group = DuplicateGroup(
            group_id="G1",
            primary_type=DuplicateType.ACOUSTIC_DUPLICATE,
            requires_manual_review=True
        )
        data = group.to_dict()
        self.assertIn("requires_manual_review", data)
        self.assertTrue(data["requires_manual_review"])

        restored = DuplicateGroup.from_dict(data)
        self.assertTrue(restored.requires_manual_review)

    def test_legacy_session_possible_duplicate_auto_protects(self):
        """TEST B: Sesión antigua sin campo requires_manual_review o con False en POSSIBLE_DUPLICATE debe terminar con True."""
        from core.models import DuplicateGroup, DuplicateType
        # Caso 1: Campo ausente
        legacy_data = {
            "group_id": "G2",
            "primary_type": DuplicateType.POSSIBLE_DUPLICATE.value,
            "tracks": []
        }
        group = DuplicateGroup.from_dict(legacy_data)
        self.assertTrue(group.requires_manual_review, "POSSIBLE_DUPLICATE debe forzar requires_manual_review=True")

        # Caso 2: Campo explícitamente False en archivo corrupto/antiguo
        corrupted_data = {
            "group_id": "G2_corrupt",
            "primary_type": DuplicateType.POSSIBLE_DUPLICATE.value,
            "requires_manual_review": False,
            "tracks": []
        }
        group2 = DuplicateGroup.from_dict(corrupted_data)
        self.assertTrue(group2.requires_manual_review, "Defensa en profundidad: POSSIBLE_DUPLICATE con False debe forzarse a True")

    def test_low_confidence_review_always_protected(self):
        """LOW_CONFIDENCE_REVIEW debe estar protegido obligatoriamente."""
        from core.models import DuplicateGroup, DuplicateType
        legacy_data = {
            "group_id": "G3",
            "primary_type": DuplicateType.LOW_CONFIDENCE_REVIEW.value,
            "requires_manual_review": False,
            "tracks": []
        }
        group = DuplicateGroup.from_dict(legacy_data)
        self.assertTrue(group.requires_manual_review, "LOW_CONFIDENCE_REVIEW debe forzar requires_manual_review=True")


if __name__ == "__main__":
    unittest.main()
