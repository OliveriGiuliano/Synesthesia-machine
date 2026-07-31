"""Output sink node definitions."""

from synesthesia_machine.nodes.output.audio import (
    GENERATE_AUDIO_TYPE_ID,
    GenerateAudioRuntime,
    create_output_definitions,
)

__all__ = ["GENERATE_AUDIO_TYPE_ID", "GenerateAudioRuntime", "create_output_definitions"]
