# Recorded WARMO example

Run `python dashboard/server.py --example` from the repository root, then open the printed address.
This mode uses only Python's standard library and starts no camera or Terse polling workers.

- `camera.jpg` is the recorded XIAO camera closeup of the plush pumpkin supplied for the demo. Its capture timestamp was not recorded.
- `state.json` contains the real `warmo-3` Mission actor state read on October 31, 2026 at 22:32:49 UTC: 27 logged steps and one resume. The JSON timestamp is the memory read time.

The photo and mission snapshot illustrate the completed demo; they are not a synchronized live feed.
Distances and headings are controller estimates. Saved goal notes describe historical steering behavior;
they are preserved as mission history, not current control instructions.

The dashboard serves these two files read-only. It sends no movement commands and does not resume,
reset, or write a mission.
