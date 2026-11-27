# Design (v1 test package)

**Goal.** Someone with only this folder and Claude Code prepares a template once, then enters a lead in n8n and gets a Loom link. Mac only.

## Decisions (made with the owner)
- Two recordings: (1) face and voice, (2) screen demo while (1) plays on speakers. Synced with Python's standard library and FFmpeg.
- Claude Code runs preparation, following `CLAUDE.md`. Daily use is n8n only, no agent.
- Video = personalised intro + **static demo** generated once and approved. No phrase detection: the end of the intro audio is the cut.
- AI voice (the user's own HeyGen clone, made in HeyGen) for both parts. Sync Lipsync 2 Pro on Fal.
- Fixed webcam crop and circle, set once per template.
- Page picture: the scroll of the lead's LinkedIn profile or another public web page, recorded by
  `capture/linkedin.mjs` before anything paid. Other websites open in an empty temporary profile; addresses that lead
  to this Mac or its network are refused. The recorder drives installed Chrome through the pipe already used for the
  Loom sign-in, so Playwright is not needed. It scrolls like trackpad swipes with pauses (13.4 s) and reads through
  the page once so that sections load. For LinkedIn it uses its own Chrome profile that the user signs in to, hides the
  message list (refusing to record if it stays visible) and stops on any sign-in wall or security check. This replaces
  the uploaded page video.
- The form asks only for plain details: `n8n-setup` builds one field per `{variable}` of the intro plus the profile
  address, and puts the intro in an editable **Intro script** node (the demo text is shown in a note).
- After that first install the intro and form belong to the user and are edited in n8n; `{Name}` is filled from the
  form field with that name. `n8n-setup` only refreshes keys unless `--replace` is given.
- Loom session: `studio.py loom` opens a Chrome/Brave window with a temporary profile; the user signs in and the
  session is read over Chrome's private debugging pipe, checked with Loom, then the profile is deleted.
- Few dependencies: FFmpeg, Python + `fal-client`, Node 22 + n8n, `whisper.cpp` for preparation only.

## Data
Everything private lives in `data/` (git-ignored): keys, tokens, Loom session, n8n, templates, jobs. Nothing is read from or written to other folders.

## Choices made by the builder (change if wrong)
- The whole Fal face goes in the bubble (no mouth mask). Needs a visual check.
- The demo is retimed with one overall speed. Optional `anchors` add matching points to the screen only, so the
  lip-synced face stays the same and adjusting clicks costs nothing.
- v1 handles one lead per n8n run through a form. CSV batches come after the end-to-end test.

## Not proven yet
Instant-clone voice quality, intro/demo voice join.
