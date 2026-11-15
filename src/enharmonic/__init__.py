"""enharmonic: real-time enharmonic pitch-spelling for MIDI input (Python port).

    Speller                      real-time; ``Speller(look_ahead=True)`` near-real-time
    spell_two_pass               offline (whole piece in hand; highest accuracy)
    spell_events                 batch helper: drive any mode over an on/off event list

A faithful port of the TypeScript ``enharmonic`` library; ``parity/`` checks it note for note.
"""

from .aug6 import OutwardPairs, outward_pairs
from .key import LocalKey, degree_keys, local_keys
from .key_side import CENTRED_SIDE_RADIUS, centred_side_keys, side_keys
from .resolution import RESOLUTION_MAX, RESOLUTION_WINDOW, ResolutionDirs, resolution_dirs
from .dominant import DOMINANT_REACH, DominantChord, dominant_chords
from .candidates import enharmonic_candidates_for
from .core import CoreSpeller
from .drive import drive, on_notes, spell_events
from .interval import Interval, interval_between, interval_label
from .kernel import resolve_exact_step, resolve_step
from .pitch import Pitch, PitchClass, midi_of, pitch_class_value, pitch_to_string
from .speller import Speller
from .two_pass import TwoPassNote, TwoPassOptions, spell_two_pass, spell_two_pass_traced

__version__ = "0.1.2"

__all__ = [
    "Speller", "CoreSpeller", "spell_two_pass", "spell_two_pass_traced", "TwoPassNote", "TwoPassOptions",
    "drive", "on_notes", "spell_events", "resolve_step", "resolve_exact_step", "outward_pairs", "OutwardPairs",
    "local_keys", "degree_keys", "LocalKey", "side_keys", "centred_side_keys", "CENTRED_SIDE_RADIUS",
    "dominant_chords", "DominantChord", "DOMINANT_REACH",
    "resolution_dirs", "ResolutionDirs", "RESOLUTION_WINDOW", "RESOLUTION_MAX",
    "Pitch", "PitchClass", "midi_of", "pitch_class_value", "pitch_to_string",
    "Interval", "interval_between", "interval_label", "enharmonic_candidates_for",
]
