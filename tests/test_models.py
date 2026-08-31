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


if __name__ == "__main__":
    unittest.main()
