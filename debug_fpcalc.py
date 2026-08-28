import sys
import os
sys.path.insert(0, os.path.abspath("."))
from tests.test_end_to_end import generate_synthetic_audio
from scipy.io import wavfile
from core.scanner import _process_audio_worker

song_a_data = generate_synthetic_audio(duration=45.0, melody_type=1)
wavfile.write("scratch_SongA.wav", 44100, song_a_data)

res = _process_audio_worker("scratch_SongA.wav")
if res:
    print("Fingerprint length:", len(res["fingerprint_raw"]))
else:
    print("Process failed")
