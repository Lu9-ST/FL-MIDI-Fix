# FL-MIDI-Fix
Cleans up FL Studio MIDI exports

## So what does this do?
This script strips out redundant and unnecessary commands/events/data from MIDI files exported from FL Studio (any version) without affecting playback and resulting in a **significantly smaller file**.

It also *attempts* to fix pitch bends when importing files back into FL Studio - if bends don't go beyond 2 semitones (up or down) then your MIDI will be imported correctly into FL with bends preserved, otherwise they'll get shrunken as usual.

### In greater detail
The script re-encodes MIDI tracks with running status (so they don't have a status byte in front of Every event), deletes duplicate tempo events, CC and pitch events being sent repeatedly, and the time signature metadata (FL only exports 4/4 no matter what)

It then checks how far pitch bends go for each channel, and if they don't exceed 200 cents (2 semitones) up or down it will set the track's RPN 0 (pitch bend range parameter) to 2, otherwise it will keep the RPN set by FL (12)

Finally, it merges track 0 with track 1 to make a single conductor track (SMT standard) so the actual tracks with note data start at 1 instead of 2.

Each step can be overridden with flags.

There are additional flags you can use to add metadata to your files that you otherwise cannot do from FL (Name, Copyright and additional text)

And general functions like stripping CC events, which if used knowingly can further reduce file size without altering playback.

Manually assigning pitch bend scale and range is also available.

### Why I made this and what FL does
For the very longest time, FL Studio had (and still has) issues with importing MIDIs that it *itself* would export - for instance it writes bank changes, but doesn't load them, but more notably, it writes MIDI files with a pitch bend range of 12. Most MIDI players will honor this and play the file just fine, but FL itself does not when importing it back. It ignores the range and goes with the GM default (2), shrinking all pitch bend events so that the max possible value up or down is 200 cents. A large amount of MIDI files get imported just fine because they never go beyond this range, but **all** FL-exported files will not, even if they stay within this range. That's where this script comes in. Even if a MIDI file has a channel with bends that go beyond 2 semitones, if it has at least one track that doesn't, that one will be preserved on import.

### What to do if your MIDI has pitch bends beyond 2 semitones
There's not much anyone (other than Image-Line) can do, really, other than one very annoying process:
- Select track
- Right click pitch bend knob
- Click "Edit events"
- Click "Tools" (wrench icon) and then "Scale levels..." (or press Alt+X)
- Drag the "Multiply" slider all the way up to 200%, then click "Accept"
- Repeat
- Repeat *again* but this time drag the "Multiply" slider down to 150%
- Done.
  
Fun, isn't it? [You should tell them just how much you enjoy it](https://www.image-line.com/contact)

## Credits
Coded with LLM assistance, supervised, checked and tested by me. Ran all of my MIDIs through it.

(does not fit my definition of vibe-coding which is "when you don't even look at/make sense of the fucking code it spits out")

Special Unthanks to Image-Line, the FL Studio team

And finally...

*Winners don't midislap!*
