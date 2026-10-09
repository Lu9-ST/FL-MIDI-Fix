# FL-MIDI-Fix
Cleans up FL Studio MIDI exports

## What does this do?
This script strips out redundant and unnecessary commands/events/data from MIDI files exported from FL Studio (any version) without affecting playback and resulting in a **significantly smaller file**.

It also *attempts* to fix pitch bends when importing files back into FL Studio - if bends don't go beyond 2 semitones (up or down) then your MIDI will be imported correctly into FL with bends preserved, otherwise they'll get shrunken.

### In greater detail
The script goes through the file, re-encodes the tracks with running status, deletes duplicate tempo change events, pitch events and the time signature metadata (FL only exports 4/4 no matter what)
It then checks how far pitch bends go in each channel, and if it does not exceed 200 cents (2 semitones) up or down it will set the MIDI's RPN to 2, otherwise it will keep the RPN set by FL (12)
Finally it merges track 0 with track 1 to make one single conductor track (SMT standard) so the actual tracks with content start at 1 instead of 2.
Each step can be overridden with flags.
There are additional flags you can use to add metadata to your files which you cannot do from FL (Name, Copyright and additional text)

### Why I made this and what FL does
FL studio is fucking stupid

Exports RPN 12 but when importing always assumes RPN 2

## Credits
yeah its ai assisted who fuckin cares i'll come back to finish this readme later cuz i actually Would rather write that myself actually.
