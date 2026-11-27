# Loom Personalizer (test version, Mac)

Enter a lead in n8n and get a Loom video: your face greets them over their page, then your demo plays.

## Start
Open this folder in Claude Code and say:

> Set me up.

Claude guides you in 8 steps and asks before anything costs money or gets published:
1. Install the tools.
2. Save your HeyGen and Fal keys (typed into a private pop-up).
3. Sign in to Loom and LinkedIn in the windows that open.
4. Create your AI voice in HeyGen.
5. Make two recordings in your own words: you speaking, then your screen while that recording plays.
6. Choose what changes for each lead: your script is taken from what you said.
7. Approve your demo.
8. Fill in your form for a first video.

After that, daily use is just the form: one field per variable you chose, plus the lead's LinkedIn profile address.
The scroll of their profile is recorded for you. You can edit the intro and the form in n8n.

## Good to know
- Everything private stays in `data/`. **Never share that folder.**
- Loom links are public. The Loom upload uses an unofficial method that Loom can change at any time.
- Paid per lead: HeyGen voice and Fal lip sync, for the intro only.
- One lead per form submission for now.
- HeyGen receives your lead details as spoken text; Fal receives footage of your face.
- Profile scrolls are recorded with your own LinkedIn account. LinkedIn does not allow automated tools: your account
  can be limited. Only LinkedIn profiles are supported.

## Not proven yet
Installing on a Mac with default npm settings or without Homebrew,
clone voice quality and the intro-to-demo voice join.
