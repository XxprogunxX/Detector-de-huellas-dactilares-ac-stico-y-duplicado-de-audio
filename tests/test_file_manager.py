import unittest
from core.models import DuplicateGroup, AudioTrack, DuplicateType, FileAction
from core.file_manager import auto_apply_recommendations

class TestFileManager(unittest.TestCase):
    def test_low_confidence_review_never_auto_deletes(self):
        # Create a group that simulates a manual review required state (best_track_path = None/Empty)
        t1 = AudioTrack(filepath="track1.mp3")
        t2 = AudioTrack(filepath="track2.mp3")
        t1.action = FileAction.UNSET
        t2.action = FileAction.UNSET
        
        # Suppose a future LOW_CONFIDENCE_REVIEW sets requires_manual_review = True
        group = DuplicateGroup(
            group_id="G1",
            primary_type=DuplicateType.LOW_CONFIDENCE_REVIEW,
            tracks=[t1, t2],
            best_track_path="",
            requires_manual_review=True
        )
        
        # Ejecutamos la acción que dispara el usuario en GUI
        auto_apply_recommendations([group])
        
        # Verify that tracks are NOT marked for deletion
        for t in group.tracks:
            self.assertNotEqual(
                t.action, 
                FileAction.DELETE, 
                f"BUG: Track {t.filepath} fue marcado como DELETE porque best_track_path era vacío."
            )

if __name__ == "__main__":
    unittest.main()
