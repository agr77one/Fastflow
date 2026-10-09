# Flowkey Meeting Companion (browser extension)

Captures Google Meet calls into your local Flowkey app with **every speaker named** —
Meet's live captions already say who is talking, so there's no "Speaker 1".
Works in Chrome and Edge. Nothing leaves your computer: the extension only talks to
`meet.google.com` (to read the page) and Flowkey at `127.0.0.1:52650`.

## Install (developer mode)

1. Open `chrome://extensions` (or `edge://extensions`).
2. Turn on **Developer mode**.
3. Click **Load unpacked** and pick this `extension` folder.
4. Pin the Flowkey icon so you can see its status.

Flowkey must be running (the extension shows *connected · vX* in its popup).

## What it does in a call

- When you join a Meet call it turns on Meet's captions (you can switch that off in the
  popup) and reads each caption line with its speaker's name, plus the participant list
  and the meeting title. It never records audio.
- Lines are sent to Flowkey once they stop changing (Meet corrects captions as you
  talk), so the transcript holds the final wording, not the first guess.
- When you leave the call the capture is finished. It shows up in the Flowkey dashboard
  under **Meetings → Captured in Chrome**; *Process now* (or the overnight batch) builds
  its digest and mind map — with real names.
- If Flowkey isn't running, captures wait in the extension and are sent later.

Tell the people in the call that you're keeping notes of it.

## If captions aren't picked up

Meet changes its page now and then. Click the extension icon → **Copy diagnostics**
during a call with captions on, and paste the result into a bug report. It lists the
page structure only (element types, labels, text lengths) — never what anyone said.

## Files

| File | Role |
|---|---|
| `manifest.json` | Manifest V3; permissions: storage, Meet pages, local Flowkey |
| `captions.js` | Turns caption snapshots into final, de-duplicated segments (tested in `tests/js/captions_test.js`) |
| `content-meet.js` | Reads the Meet page: call state, captions, participants, title |
| `background.js` | Sends captures to Flowkey (`capture_push`), queues them while Flowkey is down |
| `popup.*` | Status, settings, diagnostics |
