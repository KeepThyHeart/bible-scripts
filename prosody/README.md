# Prosody rules

`rules.json` (`kth-prosody-rules/1`) holds the settings that [`tools/prosody`](../tools/prosody/README.md)
uses to soften repeated small-word phrases where Kokoro over-stresses them.

- `version`: bump it on any change. audio-build records it, so a change re-renders the audio.
- `repeated`: what counts as a repeated phrase. `smallWords` are the only words a phrase may
  contain; `minCount` is how often it must occur in one chapter.
- `soften`: how much to correct. `marginHz` is how far below the following content word the
  phrase's pitch must stay; `durationScale` and `energyDrop` shorten and quieten an
  over-stressed word; `liftNextHz` raises the content word after it.
